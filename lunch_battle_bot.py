"""
점심대결 카톡 자동발송 봇
- 매일 10:50 실행
- 문정동 테라타워 날씨 조회 → Claude로 메뉴 2개 문구 생성 → 카카오톡 나에게 보내기
- 카카오 토큰 처리 방식은 기존 뉴스봇(news_bot.py)과 동일한 구조 사용
"""

import os
import json
import requests
from google import genai

# ── 설정 ──────────────────────────────────────────
LAT, LON = 37.4852, 127.1224  # 문정동 테라타워 근처
TOKEN_FILE = "kakao_token.json"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
KAKAO_ACCESS_TOKEN = os.getenv("KAKAO_ACCESS_TOKEN")
KAKAO_REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")

WEATHER_ICONS = {
    "폭염": "🥵🥵🥵", "무더위": "😩😩😩", "비": "😢😢😢", "쌀쌀": "🥶🥶🥶", "맑음": "😎😎😎"
}

BOLD_MAP = str.maketrans(
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭"
)

def bold(text: str) -> str:
    """숫자·영문을 유니코드 볼드체로 변환 (카톡 텍스트엔 폰트 크기 조절이 없어 대신 사용)"""
    return text.translate(BOLD_MAP)

# ── 카카오 토큰 관리 (뉴스봇과 동일 구조) ───────────────
def load_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return {
        "access_token": KAKAO_ACCESS_TOKEN,
        "refresh_token": KAKAO_REFRESH_TOKEN,
    }


def save_tokens(tokens: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def refresh_access_token(refresh_token: str):
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": KAKAO_REST_API_KEY,
        "refresh_token": refresh_token,
    }
    res = requests.post(url, data=data)
    if res.status_code == 200:
        print("✅ 카카오 토큰 갱신 완료")
        return res.json()
    else:
        print(f"❌ 토큰 갱신 실패: {res.text}")
        return None


def get_valid_access_token():
    tokens = load_tokens()
    refreshed = refresh_access_token(tokens["refresh_token"])
    if refreshed:
        new_access = refreshed["access_token"]
        new_refresh = refreshed.get("refresh_token", tokens["refresh_token"])
        save_tokens({"access_token": new_access, "refresh_token": new_refresh})
        return new_access
    return tokens["access_token"]  # 갱신 실패 시 기존 토큰으로 시도


# ── 날씨 조회 (Open-Meteo, 키 불필요) ─────────────────
def get_weather():
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        f"&current=temperature_2m,relative_humidity_2m,precipitation,weather_code"
        f"&timezone=Asia%2FSeoul"
    )
    r = requests.get(url, timeout=10).json()
    cur = r["current"]
    temp = round(cur["temperature_2m"])
    humidity = round(cur["relative_humidity_2m"])
    precip = cur["precipitation"]
    code = cur["weather_code"]

    is_rain = precip > 0 or code in range(51, 68)
    if is_rain:
        mood = "비"
    elif temp >= 33:
        mood = "폭염"
    elif temp >= 29:
        mood = "무더위"
    elif temp <= 10:
        mood = "쌀쌀"
    else:
        mood = "맑음"

    return {"temp": temp, "humidity": humidity, "is_rain": is_rain, "mood": mood}


# ── Gemini로 메뉴 문구 생성 ────────────────────────────
def generate_copy(weather):
    client = genai.Client(api_key=GEMINI_API_KEY)

    weather_line = bold(f"{weather['temp']}°C · 습도 {weather['humidity']}%")
    if weather["is_rain"]:
        weather_line += bold(" · 비")
    icon = WEATHER_ICONS[weather["mood"]]

    prompt = f"""오늘 점심 메뉴 추천 콘텐츠를 아래 형식 그대로 딱 맞춰 작성해줘.
다른 설명 없이 형식 그대로 결과만 출력해.

형식:
1️⃣ [감각묘사, 의성어·의태어 2~3개 포함] [자연스러운 연결어로] [음식명] [이모티콘]
2️⃣ [감각묘사, 의성어·의태어 2~3개 포함] [자연스러운 연결어로] [음식명] [이모티콘]

…오늘 점심 [자연스러운 질문]! 댓글에 번호! 👀

규칙:
- 오늘 날씨 분위기: {weather['mood']}
- 상황 설명 문장은 절대 넣지 말 것
- 의성어·의태어를 문장마다 2~3개씩 넣을 것 (예: 살얼음 동동, 후루룩, 아삭아삭 / 지글지글, 매콤알싸, 쫄깃쫄깃 등 재미있고 다양하게)
- "~하고 싶은~~~"을 반복하지 말고, 매번 다른 자연스러운 연결어를 쓸 것
  (예: "~까지 완벽한", "~가시는", "~부르는", "~제맛인", "~당기는", "~반하는" 등, 앞 문장과 매끄럽게 이어지도록)
- 마지막 질문은 반드시 "오늘 점심"으로 시작하고, 매번 다른 표현으로 쓸 것
  (예: "오늘 점심 뭐 먹을까?", "오늘 점심 뭐가 땡겨요?", "오늘 점심 뭘로 할까요?", "오늘 점심은 뭘로?")
- 두 메뉴는 서로 다른 카테고리로 대비되게"""

    res = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    body = res.text.strip()
    return f"{weather_line} {icon}\n\n{body}"


# ── 카카오톡 나에게 보내기 ──────────────────────────────
def send_to_kakao(text):
    access_token = get_valid_access_token()
    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": "https://www.kakao.com", "mobile_web_url": "https://www.kakao.com"},
    }
    res = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template)},
    )
    if res.status_code == 200:
        print("✅ 카톡 전송 완료")
    else:
        print(f"❌ 카톡 전송 실패: {res.text}")


# ── 실행 ─────────────────────────────────────────
if __name__ == "__main__":
    weather = get_weather()
    message = generate_copy(weather)
    print(message)
    send_to_kakao(message)
