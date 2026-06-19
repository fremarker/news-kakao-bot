"""
AI & 국제정치 뉴스 자동 요약 → 카카오톡 발송 봇 (기사별 개별 발송 버전)
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
import re
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

TOKEN_FILE   = "kakao_token.json"
KST          = timezone(timedelta(hours=9))
ARTICLES_PER_CATEGORY = 3   # 카테고리당 발송할 기사 수

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
# 뉴스 수집 + 화제성 점수화
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

    articles.sort(key=lambda x: x["coverage_count"], reverse=True)

    for a in articles:
        del a["_kw"]

    return articles

# ──────────────────────────────────────────────
# Gemini 요약 (기사별 JSON 구조로 반환)
# ──────────────────────────────────────────────

def curate_with_gemini(articles: list, category: str, n: int = ARTICLES_PER_CATEGORY) -> list:
    """화제성 높은 기사 n개를 골라 각각 제목/요약/인사이트가 담긴 딕셔너리 리스트로 반환"""
    if not articles:
        return []

    articles_text = ""
    for i, a in enumerate(articles[:15], 1):
        coverage = a.get("coverage_count", 1)
        articles_text += f"{i}. [{a['source']}] (유사 보도 매체 수: {coverage}) {a['title']}\n"
        if a["summary"]:
            articles_text += f"   {a['summary']}\n"
        articles_text += f"   링크: {a['link']}\n\n"

    prompt = f"""당신은 SNS(스레드) 클릭률을 중시하는 뉴스 큐레이터입니다.
아래는 오늘의 {category} 관련 최신 해외 뉴스이며, "유사 보도 매체 수"는 같은 주제를
여러 매체가 동시에 다룬 정도(화제성 신호)입니다.

[뉴스 목록]
{articles_text}

유사 보도 매체 수가 높은 것(화제성 큼) + 임팩트가 큰 사건을 우선 고려해서,
가장 핫한 뉴스 {n}개를 선별하고, 아래 JSON 배열 형식으로만 응답하세요.
다른 설명, 인사말, 마크다운 코드블록 표시 없이 순수 JSON 배열만 출력하세요.

각 항목 구조:
{{
  "title": "팩트에 기반하되 클릭하고 싶게 만드는 자극적이고 흥미로운 한 줄 제목 (낚시성 거짓 금지, 사실 왜곡 없이 호기심 자극)",
  "summary": "핵심만 담은 한 문장 요약",
  "insight": "아래 4가지 관점 중 이 기사에 가장 잘 맞는 1~2개를 골라 2줄로 작성한 통찰 (추측은 '~할 가능성' 등으로 신중하게 표현). 관점: (a)파급력-다른 산업/국가/사람들에게 미칠 연쇄효과 (b)숨은 맥락-표면적 발표 뒤 진짜 의도나 배경 (c)선례 비교-과거 비슷한 사례와 다른 점 (d)다음 단계-이후 일어날 가능성이 높은 일",
  "source": "원문 매체명 그대로 (예: TechCrunch, Reuters)",
  "link": "원문 링크 그대로"
}}

전문 용어는 한국어로 자연스럽게 번역하세요. 반드시 유효한 JSON 배열로만 응답하세요.
"""

    max_retries = 4
    wait_times  = [30, 60, 120]  # 1차실패→30초, 2차실패→60초, 3차실패→120초 대기
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt,
            )
            text = response.text.strip()
            # 코드블록 표시가 섞여 나오는 경우 제거
            text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed[:n]
            return []
        except Exception as e:
            print(f"❌ Gemini 오류 (시도 {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                wait = wait_times[attempt - 1]
                print(f"   ⏳ {wait}초 후 재시도...")
                time.sleep(wait)
            else:
                return []

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

def send_with_auto_refresh(message: str) -> bool:
    tokens       = load_tokens()
    access_token = tokens.get("access_token", "")

    success = send_kakao_message(access_token, message)

    if not success:
        print("🔄 토큰 갱신 중...")
        new_tokens = refresh_access_token(tokens.get("refresh_token", ""))
        if new_tokens:
            tokens["access_token"] = new_tokens["access_token"]
            if "refresh_token" in new_tokens:
                tokens["refresh_token"] = new_tokens["refresh_token"]
            save_tokens(tokens)
            success = send_kakao_message(tokens["access_token"], message)

    return success

# ──────────────────────────────────────────────
# 메시지 포맷 (기사 1개 = 카톡 1개)
# ──────────────────────────────────────────────

def build_article_message(icon: str, category: str, idx: int, total: int, article: dict) -> str:
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    title    = article.get("title", "(제목 없음)")
    summary  = article.get("summary", "")
    insight  = article.get("insight", "")
    source   = article.get("source", "")
    link     = article.get("link", "")

    return (
        f"{icon} [{category}] ({idx}/{total})\n"
        f"🕐 {now_kst} KST\n"
        f"{'─' * 22}\n"
        f"📌 {title}\n\n"
        f"{summary}\n\n"
        f"💡 인사이트\n{insight}\n\n"
        f"출처: {source}\n"
        f"🔗 {link}"
    )

def send_category(icon: str, category: str, feeds: list):
    print(f"\n📡 {category} 뉴스 수집 중...")
    articles = fetch_articles(feeds)
    print(f"   → {len(articles)}건 수집")

    print(f"🤖 {category} 뉴스 선별 + 요약 중 (Gemini)...")
    curated = curate_with_gemini(articles, category)
    print(f"   → {len(curated)}건 선별")

    if not curated:
        print(f"   ⚠️  선별 실패, 오류 메시지 발송")
        msg = f"{icon} [{category}] 뉴스 요약\n오늘은 요약 생성에 실패했습니다."
        send_with_auto_refresh(msg)
        return

    total = len(curated)
    for idx, article in enumerate(curated, 1):
        msg = build_article_message(icon, category, idx, total, article)
        print(f"📨 카카오톡 발송 ({category} {idx}/{total})...")
        ok = send_with_auto_refresh(msg)
        print(f"   → {'✅ 성공' if ok else '❌ 실패'}")
        if idx < total:
            time.sleep(3)  # 기사 간 짧은 대기

# ──────────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────────

def run():
    print(f"\n{'='*50}")
    print(f"🚀 뉴스봇 시작: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST")
    print(f"{'='*50}")

    send_category("🤖", "AI·인공지능", AI_FEEDS)

    time.sleep(20)  # 카테고리 간 Gemini 호출 제한 회피

    send_category("🌐", "국제정치·외교", GEOPOLITICS_FEEDS)

    print(f"\n✅ 완료: {datetime.now(KST).strftime('%H:%M:%S')}")

if __name__ == "__main__":
    run()
