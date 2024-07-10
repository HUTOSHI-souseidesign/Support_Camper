import os
import json
import requests
from datetime import datetime
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, MessageAction,
    CarouselTemplate, CarouselColumn, URIAction, TemplateSendMessage
)

# Initialize Line API
LINE_CHANNEL_ACCESS_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
LINE_CHANNEL_SECRET = os.environ['LINE_CHANNEL_SECRET']
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Load campsite and equipment data
with open('campsite.json', 'r') as f:
    CAMPSITE_DATA = json.load(f)['campsites']

# Convert feature string to list for each campsite
for campsite in CAMPSITE_DATA:
    campsite['feature'] = campsite['feature'].split()

with open('equipment_list.json', 'r') as f:
    EQUIPMENT_DATA = json.load(f)['equipment_sets']

# User state management
user_states = {}
user_preferences = {}

# City IDs for weather forecast
CITY_IDS = {
    "下関": "350010",
    "山口": "350020",
    "柳井": "350030",
    "萩": "350040"
}

def lambda_handler(event, context):
    body = event
    
    try:
        for line_event in body['events']:
            if line_event['type'] == 'message' and line_event['message']['type'] == 'text':
                handle_text_message(line_event)
    except Exception as e:
        print(f"Error processing event: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps('Internal server error')}
    
    return {'statusCode': 200, 'body': json.dumps('OK')}

def handle_text_message(event):
    user_id = event['source']['userId']
    text = event['message']['text']
    reply_token = event['replyToken']
    
    if user_id not in user_states:
        user_states[user_id] = 'INIT'
        user_preferences[user_id] = {}
    
    state = user_states[user_id]
    
    if text.startswith("天気予報"):
        location = text.split()[1]
        if location in CITY_IDS:
            weather_info = get_weather_info(CITY_IDS[location])
            if weather_info:
                weather_message = format_weather_message(weather_info)
                line_bot_api.reply_message(reply_token, weather_message)
            else:
                send_message(reply_token, "天気情報の取得に失敗しました。")
        else:
            send_message(reply_token, "指定された地域の天気情報は利用できません。")
        return

    if state == 'INIT':
        user_states[user_id] = 'ASK_EQUIPMENT'
        send_message(reply_token, "こんにちは！山口県内のキャンプ場をおすすめします。キャンプ道具を持っていますか？", ["持ってる", "持ってない"])
    elif state == 'ASK_EQUIPMENT':
        user_preferences[user_id]['level'] = "道具あり" if text == "持ってる" else "道具なし"
        user_states[user_id] = 'ASK_PRICE'
        send_message(reply_token, "キャンプ場は無料と有料のどちらがいいですか？", ["無料", "有料"])
    elif state == 'ASK_PRICE':
        user_preferences[user_id]['price'] = text
        user_states[user_id] = 'ASK_FEATURE'
        send_message(reply_token, "どこでキャンプしたいですか？", ["山", "海", "川", "湖"])
    elif state == 'ASK_FEATURE':
        user_preferences[user_id]['feature'] = text
        recommend_campsites(reply_token, user_id)
        user_states[user_id] = 'INIT'
        messages = TextSendMessage(text='新しく始める場合は何かメッセージを送ってください。')
        line_bot_api.push_message(user_id, messages)
    else:
        send_message(reply_token, "新しく始める場合は何かメッセージを送ってください。")

def recommend_campsites(reply_token, user_id):
    preferences = user_preferences[user_id]
    suitable_campsites = [
        site for site in CAMPSITE_DATA
        if site['level'] == preferences['level'] and
        preferences['feature'] in site['feature'] and
        site['price'] == preferences['price']
    ]
    
    if not suitable_campsites:
        send_message(reply_token, "申し訳ありません。条件に合うキャンプ場が見つかりませんでした。")
        return
    
    carousel_columns = []
    for site in suitable_campsites[:3]:  # Limit to 3 campsites
        column = CarouselColumn(
            thumbnail_image_url=site['photo_url'] if site['photo_url'] else None,
            title=site['name'],
            text=f"{' '.join(site['feature'])}のキャンプ場（{site['level']}向け）",
            actions=[
                URIAction(label='Google Map', uri=site['google_map_url']),
                URIAction(label='詳細情報', uri=site['site_url']),
                MessageAction(label='天気予報', text=f"天気予報 {site['location']}")
            ]
        )
        carousel_columns.append(column)
    
    carousel_template = CarouselTemplate(columns=carousel_columns)
    carousel_message = TemplateSendMessage(alt_text='おすすめキャンプ場', template=carousel_template)
    
    equipment_message = TextSendMessage(text=f"おすすめの装備:\n{', '.join(EQUIPMENT_DATA[suitable_campsites[0]['equipment_set']])}")
    
    line_bot_api.reply_message(reply_token, [carousel_message, equipment_message])

def send_message(reply_token, text, quick_reply_items=None):
    message = TextSendMessage(text=text)
    if quick_reply_items:
        message.quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label=item, text=item))
            for item in quick_reply_items
        ])
    line_bot_api.reply_message(reply_token, message)

def get_weather_info(city_id):
    api_url = f"https://weather.tsukumijima.net/api/forecast/city/{city_id}"
    
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        weather_data = response.json()
        
        area_name = weather_data["location"]["city"]
        
        forecast = []
        
        for forecast_day in weather_data["forecasts"][:3]:  # 3日間の予報に制限
            date = datetime.strptime(forecast_day["date"], '%Y-%m-%d').strftime('%Y年%m月%d日 (%a)')
            weather = forecast_day["telop"]
            temp_min = forecast_day["temperature"]["min"]["celsius"]
            temp_max = forecast_day["temperature"]["max"]["celsius"]
            
            if temp_min is None and temp_max is None:
                temperature = "N/A"
            elif temp_min is None:
                temperature = f"-/{temp_max}"
            elif temp_max is None:
                temperature = f"{temp_min}/-"
            else:
                temperature = f"{temp_min}/{temp_max}"
            
            forecast.append({
                "date": date,
                "weather": weather,
                "temperature": temperature
            })
        
        return {"area_name": area_name, "forecast": forecast}
    
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        return None

def format_weather_message(weather_info):
    message = f"地域: {weather_info['area_name']}\n\n"
    message += "日付         天気      気温\n"
    message += "-" * 30 + "\n"
    for day in weather_info['forecast']:
        message += f"{day['date']} {day['weather']:<10} {day['temperature']}°C\n"
    return TextSendMessage(text=message)
