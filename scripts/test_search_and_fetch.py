import os
import sys

# 将父目录加入 path，以便导入 football_advisor
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from football_advisor.config import load_config
from football_advisor.search_router import SearchRouter
from football_advisor.text_fetcher import TextFetcher


def main():
    fetcher = TextFetcher(timeout_seconds=15, min_text_length=150, require_title=True)

    # 绕过 SearXNG 的 403/429 限制，直接测试几个真实的足球新闻网页来校准正文抓取
    test_urls = [
        "https://www.skysports.com/football/news/11095/13106567/manchester-city-injury-news-pep-guardiola-latest",
        "https://www.bbc.com/sport/football/articles/cd10x1dqp1no",
        "https://www.espn.com/soccer/report/_/gameId/697148",
        # 故意放一个不是新闻或者很短的页面
        "https://example.com",
    ]

    print(f"开始抓取 {len(test_urls)} 个测试 URL...")
    docs = fetcher.fetch_many(test_urls)

    print("\n抓取结果统计:")
    for doc in docs:
        print(f" - URL: {doc.url}")
        print(f"   状态: {doc.status}")
        if doc.status == "ok":
            print(f"   标题: {doc.title}")
            print(f"   正文长度: {len(doc.text)}")
            print(f"   正文片段: {doc.text[:100]}...")
        elif doc.error:
            print(f"   错误: {doc.error}")
        print()


if __name__ == "__main__":
    main()
