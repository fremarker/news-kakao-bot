"""
AI & 국제정치 뉴스 자동 요약 → 카카오톡 발송 봇 (Gemini 신버전)
----------------------------------------------
필요 패키지:
  pip install feedparser requests python-dotenv google-genai

실행 전 .env 파일에 아래 값 설정:
  GEMINI_API_KEY=AQ....
  KAKAO_REST_API_KEY=...
"""

import os
import json
import time
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from google import genai

load_dotenv()

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")
KAKAO_ACCESS_TOKEN = os.getenv("KAKAO_ACCESS_TOKEN")
KAKAO_REFRESH_TOKEN= os.getenv("KAKAO_REFRESH_TOKEN")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")

TOKEN_FILE = "kakao_token.json"
KST = timezone(timedelta(hours=9))

# Gemini 클라이언트
client = genai.Client(api_key=GEMINI_API_KEY)

# ──────────────────────────────────────────────
# RSS 피드 목록
# ──────────────────────────────────────────────

AI_FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence+OR+ChatGPT+OR+LLM&hl=en&gl=US&ceid=US:en",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/ai/feed/",
    "https://www.technologyreview.com/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
]

GEOPOLITICS_FEEDS = [
    "https://news.google.com/rss/search?q=international+politics+OR+diplomacy+OR+geopolitics&hl=en&gl=US&ceid=US:en",
    "https://feeds.reuters.com/reuters/worldNews",
    "http://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.foreignaffairs.com/rss.xml",
    "https://apnews.com/rss/world-news",
]

# ──────────────────────────────────────────────
# 카카오 토큰 관리
# ──────────────────────────────────────────────

def load_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return {
        "access_token":  KAKAO_ACCESS_TOKEN,
        "refresh_token": KAKAO_REFRESH_TOKEN,
    }

def save_tokens(tokens: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

def refresh_access_token(refresh_token: str):
    url  = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type":    "refresh_token",
        "client_id":     KAKAO_REST_API_KEY,
        "refresh_token": refresh_token,
    }
    res = requests.post(url, data=data)
    if res.status_code == 200:
        print("✅ 카카오 토큰 갱신 완료")
        return res.json()
    else:
        print(f"❌ 토큰 갱신 실패: {res.text}")
        return None

# ──────────────────────────────────────────────
# 뉴스 수집
# ──────────────────────────────────────────────

def fetch_articles(feeds: list, max_per_feed: int = 8) -> list:
    articles    = []
    seen_titles = set()

    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                title   = entry.get("title", "").strip()
                link    = entry.get("link", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()

                if title and title not in seen_titles:
                    seen_titles.add(title)
                    articles.append({
                        "title":   title,
                        "link":    link,
                        "summary": summary[:300] if summary else "",
                        "source":  feed.feed.get("title", url),
                    })
        except Exception as e:
            print(f"⚠️  피드 오류 ({url[:50]}...): {e}")

    return score_by_coverage(articles)

def _keywords(text: str) -> set:
    """제목에서 핵심 키워드(4글자 이상 단어) 추출 — 단순 중복 주제 탐지용"""
    words = text.lower().replace(",", " ").replace(".", " ").split()
    return {w for w in words if len(w) >= 4}

def score_by_coverage(articles: list) -> list:
    """같은 주제를 다루는 매체 수가 많을수록 높은 점수 부여 (화제성 신호)"""
    for a in articles:
        a["_kw"] = _keywords(a["title"])

    for a in articles:
        covering_sources = set()
        for b in articles:
            if a["_kw"] and b["_kw"]:
                overlap = len(a["_kw"] & b["_kw"]) / max(len(a["_kw"]), 1)
                if overlap >= 0.4:
                    covering_sources.add(b["source"])
        a["coverage_count"] = len(covering_sources)

    # coverage_count 내림차순 정렬 (같은 주제를 많이 다룰수록 앞으로)
    articles.sort(key=lambda x: x["coverage_count"], reverse=True)

    for a in articles:
        del a["_kw"]

    return articles

# ──────────────────────────────────────────────
# Gemini 요약
# ──────────────────────────────────────────────

def summarize_with_gemini(articles: list, category: str) -> str:
    if not articles:
        return "관련 뉴스를 찾지 못했습니다."

    articles_text = ""
    for i, a in enumerate(articles[:15], 1):
        coverage = a.get("coverage_count", 1)
        articles_text += f"{i}. [{a['source']}] (유사 보도 매체 수: {coverage}) {a['title']}\n"
        if a["summary"]:
            articles_text += f"   {a['summary']}\n"
        articles_text += f"   🔗 {a['link']}\n\n"

    prompt = f"""당신은 SNS(스레드) 클릭률을 중시하는 뉴스 큐레이터입니다.
아래는 오늘의 {category} 관련 최신 해외 뉴스이며, "유사 보도 매체 수"는 같은 주제를
여러 매체가 동시에 다룬 정도(화제성 신호)입니다.

[뉴스 목록]
{articles_text}

다음 기준으로 한국어 요약을 작성해주세요:

1. 유사 보도 매체 수가 높은 것(화제성 큼) + 임팩트가 큰 사건을 우선 고려해서, 가장 핫한 뉴스 7개를 선별하세요.
2. 각 뉴스마다 아래 형식을 지키세요:
   - 제목: 팩트에 기반하되, 클릭하고 싶게 만드는 자극적이고 흥미로운 한 줄 제목 (낚시성 거짓은 금지, 사실 왜곡 없이 호기심을 자극하는 표현 사용)
   - 요약: 핵심만 한 문장으로 짧게 (지금까지보다 훨씬 간결하게, 군더더기 없이)
   - 출처는 원문 그대로 표기 (예: [TechCrunch], [Reuters])
   - 원문 링크 포함
3. 트렌드 정리, 부가 설명은 생략하세요.

전문 용어는 한국어로 자연스럽게 번역하고, 전체 분량은 최대한 짧게 유지해 7개 항목이 모두 들어가도록 하세요.
"""

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt,
            )
            return response.text
        except Exception as e:
            print(f"❌ Gemini 오류 (시도 {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                wait = 15 * attempt
                print(f"   ⏳ {wait}초 후 재시도...")
                time.sleep(wait)
            else:
                return "요약 생성 중 오류가 발생했습니다."

# ──────────────────────────────────────────────
# 카카오톡 발송
# ──────────────────────────────────────────────

def send_kakao_message(access_token: str, message: str) -> bool:
    url     = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/x-www-form-urlencoded",
    }
    if len(message) > 1990:
        message = message[:1987] + "..."

    template = {
        "object_type": "text",
        "text":        message,
        "link": {
            "web_url":        "https://news.google.com",
            "mobile_web_url": "https://news.google.com",
        },
        "button_title": "구글 뉴스 열기",
    }
    data = {"template_object": json.dumps(template)}
    res  = requests.post(url, headers=headers, data=data)

    if res.status_code == 200:
        return True
    elif res.status_code == 401:
        print("⚠️  토큰 만료 감지")
        return False
    else:
        print(f"❌ 카카오 발송 오류: {res.status_code} {res.text}")
        return False

def split_message(message: str, chunk_size: int = 1900) -> list:
    """긴 메시지를 카카오톡 글자수 제한에 맞춰 여러 개로 분할"""
    if len(message) <= chunk_size:
        return [message]

    chunks = []
    lines  = message.split("\n")
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > chunk_size:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line

    if current:
        chunks.append(current)

    total = len(chunks)
    return [f"{c}\n\n({i+1}/{total})" for i, c in enumerate(chunks)]

def send_with_auto_refresh(message: str):
    tokens       = load_tokens()
    access_token = tokens.get("access_token", "")

    chunks = split_message(message)
    all_success = True

    for idx, chunk in enumerate(chunks):
        success = send_kakao_message(access_token, chunk)

        if not success:
            print("🔄 토큰 갱신 중...")
            new_tokens = refresh_access_token(tokens.get("refresh_token", ""))
            if new_tokens:
                tokens["access_token"] = new_tokens["access_token"]
                if "refresh_token" in new_tokens:
                    tokens["refresh_token"] = new_tokens["refresh_token"]
                save_tokens(tokens)
                success = send_kakao_message(tokens["access_token"], chunk)

        all_success = all_success and success

        if idx < len(chunks) - 1:
            time.sleep(2)  # 분할 메시지 사이 짧은 대기

    return all_success

# ──────────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────────

def build_message(icon: str, category: str, summary: str) -> str:
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    header  = f"{icon} [{category}] 뉴스 요약\n🕐 {now_kst} KST\n"
    divider = "─" * 30 + "\n"
    return header + divider + summary

def run():
    print(f"\n{'='*50}")
    print(f"🚀 뉴스봇 시작: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST")
    print(f"{'='*50}\n")

    # ── 1. AI 뉴스 ──
    print("📡 AI 뉴스 수집 중...")
    ai_articles = fetch_articles(AI_FEEDS)
    print(f"   → {len(ai_articles)}건 수집")

    print("🤖 AI 뉴스 요약 중 (Gemini)...")
    ai_summary = summarize_with_gemini(ai_articles, "AI·인공지능")
    ai_message = build_message("🤖", "AI·인공지능", ai_summary)

    print("📨 카카오톡 발송 (AI 뉴스)...")
    ok = send_with_auto_refresh(ai_message)
    print(f"   → {'✅ 성공' if ok else '❌ 실패'}")

    time.sleep(20)

    # ── 2. 국제정치 뉴스 ──
    print("\n📡 국제정치 뉴스 수집 중...")
    geo_articles = fetch_articles(GEOPOLITICS_FEEDS)
    print(f"   → {len(geo_articles)}건 수집")

    print("🤖 국제정치 뉴스 요약 중 (Gemini)...")
    geo_summary = summarize_with_gemini(geo_articles, "국제정치·외교")
    geo_message = build_message("🌐", "국제정치·외교", geo_summary)

    print("📨 카카오톡 발송 (국제정치 뉴스)...")
    ok = send_with_auto_refresh(geo_message)
    print(f"   → {'✅ 성공' if ok else '❌ 실패'}")

    print(f"\n✅ 완료: {datetime.now(KST).strftime('%H:%M:%S')}")

if __name__ == "__main__":
    run()
