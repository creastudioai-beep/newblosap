#!/usr/bin/env python3
"""
Парсер блога sochi-autoparts.blogspot.com
Получает все посты из Blogger через Atom API и сохраняет в JSON
для корректного отображения на сайте sochiautoparts.ru

Использование:
    python3 blogger_parser.py                        # базовый запуск
    python3 blogger_parser.py --output posts.json    # указать выходной файл
    python3 blogger_parser.py --clean                # очистить HTML от мусора
    python3 blogger_parser.py --per-page 25          # кол-во постов на запрос
    python3 blogger_parser.py --extract-images       # извлечь список изображений
"""

import xml.etree.ElementTree as ET
import json
import re
import html as html_module
import argparse
from datetime import datetime, timezone
from urllib.parse import urlparse, urlencode
from html.parser import HTMLParser


# ─────────────────────────────────────────────────────────────
# Blogger Atom API namespaces
# ─────────────────────────────────────────────────────────────
NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'openSearch': 'http://a9.com/-/spec/opensearchrss/1.0/',
    'blogger': 'http://schemas.google.com/blogger/2008',
    'gd': 'http://schemas.google.com/g/2005',
    'thr': 'http://purl.org/syndication/thread/1.0',
}

BLOG_URL = 'https://sochi-autoparts.blogspot.com'
BLOG_FEED_URL = f'{BLOG_URL}/feeds/posts/default'


# ─────────────────────────────────────────────────────────────
# HTML cleaning utilities
# ─────────────────────────────────────────────────────────────
class HTMLTextExtractor(HTMLParser):
    """Извлекает чистый текст из HTML, сохраняя структуру абзацев."""

    def __init__(self):
        super().__init__()
        self.result = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip = False
        if tag in ('p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr'):
            self.result.append('\n')

    def handle_data(self, data):
        if not self._skip:
            self.result.append(data)

    def get_text(self):
        return ''.join(self.result).strip()


def clean_blogger_html(raw_html: str) -> str:
    """
    Очищает HTML от Blogger/MSO мусора и возвращает чистый HTML
    для отображения на сайте.
    """
    if not raw_html:
        return ''

    # Декодируем HTML-сущности (Blogger хранит контент с &lt; &gt; и т.д.)
    text = html_module.unescape(raw_html)

    # Удаляем MSO conditional комментарии <!--[if ...]>...<![endif]-->
    text = re.sub(r'<!--\[if[\s\S]*?<!\[endif\]-->', '', text)

    # Удаляем XML/Word разметку
    text = re.sub(r'<\?xml[\s\S]*?\?>', '', text)
    text = re.sub(r'<w:[^>]+>.*?</w:[^>]+>', '', text, flags=re.DOTALL)
    text = re.sub(r'<o:[^>]+>.*?</o:[^>]+>', '', text, flags=re.DOTALL)
    text = re.sub(r'<v:[^>]+>.*?</v:[^>]+>', '', text, flags=re.DOTALL)
    text = re.sub(r'<m:[^>]+>.*?</m:[^>]+>', '', text, flags=re.DOTALL)

    # Удаляем Blogger separator div'ы с пустыми линками (используются как обёртки изображений)
    # Но сохраняем сами изображения
    text = re.sub(
        r'<div\s+class="separator"[^>]*>\s*<a[^>]*>\s*<img\s+([^>]+)/>\s*</a>\s*</div>',
        r'<img \1/>',
        text,
        flags=re.DOTALL
    )

    # Удаляем пустые параграфы
    text = re.sub(r'<p>\s*</p>', '', text)
    text = re.sub(r'<p>&nbsp;</p>', '', text)
    text = re.sub(r'<p>\s*&nbsp;\s*</p>', '', text)

    # Удаляем Blogger-specific классы и атрибуты
    text = re.sub(r'\s*class="[^"]*blogger-[^\"]*"', '', text)
    text = re.sub(r'\s*data-original-height="[^"]*"', '', text)
    text = re.sub(r'\s*data-original-width="[^"]*"', '', text)
    text = re.sub(r'\s*imageanchor="[^"]*"', '', text)

    # Нормализуем br-теги
    text = re.sub(r'<br\s*/?>', '<br/>', text)

    # Удаляем множественные пустые строки
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Убираем &nbsp; в начале/конце строк
    text = re.sub(r'^\s*&nbsp;\s*', '', text, flags=re.MULTILINE)

    return text.strip()


def extract_images(html_content: str) -> list:
    """Извлекает все URL изображений из HTML контента."""
    if not html_content:
        return []
    text = html_module.unescape(html_content)
    # Ищем src в img тегах и href в separator-ссылках на изображения
    img_urls = re.findall(r'src=["\']([^"\']+\.(?:jpg|jpeg|png|gif|webp|bmp|svg))["\']', text, re.IGNORECASE)
    # Также ищем в a-tag href с изображениями
    href_imgs = re.findall(r'href=["\']([^"\']+\.(?:jpg|jpeg|png|gif|webp))["\']', text, re.IGNORECASE)
    all_urls = list(dict.fromkeys(img_urls + href_imgs))  # уникальные, сохраняя порядок
    return all_urls


def extract_first_image(html_content: str) -> str | None:
    """Извлекает URL первого изображения для превью."""
    images = extract_images(html_content)
    return images[0] if images else None


def html_to_plain_text(html_content: str) -> str:
    """Конвертирует HTML в чистый текст."""
    if not html_content:
        return ''
    text = html_module.unescape(html_content)
    extractor = HTMLTextExtractor()
    try:
        extractor.feed(text)
    except Exception:
        return re.sub(r'<[^>]+>', ' ', text).strip()
    result = extractor.get_text()
    # Нормализуем пробелы
    result = re.sub(r'\n{3,}', '\n\n', result)
    result = re.sub(r'[ \t]+', ' ', result)
    return result.strip()


def generate_slug(title: str) -> str:
    """Генерирует URL-совместимый slug из заголовка."""
    slug = title.strip().lower()
    # Транслитерация базовых кириллических символов
    translit_map = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        ' ': '-', ':': '-', ',': '-', '.': '-', '(': '', ')': '',
        '/': '-', '\\': '-', '—': '-', '–': '-', '«': '', '»': '',
        '"': '', "'": '', '!': '', '?': '', ';': '', '#': '', '@': '',
        '&': '-and-', '+': '', '%': '', '*': '', '=': '',
    }
    result = ''
    for char in slug:
        result += translit_map.get(char, char)
    # Убираем множественные дефисы и обрезаем
    result = re.sub(r'-{2,}', '-', result)
    result = re.sub(r'^-+|-+$', '', result)
    return result[:80] if result else 'untitled'


def parse_iso_date(date_str: str) -> str | None:
    """Парсит ISO дату и возвращает в формате ISO 8601 (UTC)."""
    if not date_str:
        return None
    try:
        # Blogger формат: 2026-04-21T22:38:00.000-07:00
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    except (ValueError, TypeError):
        return None


def count_words(text: str) -> int:
    """Считает количество слов в тексте."""
    if not text:
        return 0
    words = re.findall(r'\b\w+\b', text)
    return len(words)


def generate_description(html_content: str, title: str, max_length: int = 200) -> str:
    """Генерирует описание из начала статьи."""
    plain = html_to_plain_text(html_content)
    if not plain:
        return title
    # Убираем дублирование заголовка
    if plain.startswith(title):
        plain = plain[len(title):].strip()
    # Обрезаем до max_length
    if len(plain) > max_length:
        plain = plain[:max_length].rstrip()
        # Обрезаем до последнего целого слова
        last_space = plain.rfind(' ')
        if last_space > max_length * 0.7:
            plain = plain[:last_space]
        plain += '...'
    return plain


# ─────────────────────────────────────────────────────────────
# Feed fetcher (использует urllib, без внешних зависимостей)
# ─────────────────────────────────────────────────────────────
def fetch_feed(url: str, max_results: int = 500) -> str:
    """Загружает Atom feed по URL. Работает без внешних зависимостей."""
    from urllib.request import urlopen, Request
    from urllib.error import URLError

    params = {'max-results': str(max_results)}
    full_url = f'{url}?{urlencode(params)}'

    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; BloggerParser/1.0; +https://sochiautoparts.ru)',
        'Accept': 'application/atom+xml, text/xml, application/xml, */*',
    }

    req = Request(full_url, headers=headers)

    try:
        with urlopen(req, timeout=30) as response:
            return response.read().decode('utf-8')
    except URLError as e:
        raise RuntimeError(f'Не удалось загрузить feed: {e.reason}') from e
    except Exception as e:
        raise RuntimeError(f'Ошибка загрузки: {e}') from e


# ─────────────────────────────────────────────────────────────
# Main parser
# ─────────────────────────────────────────────────────────────
def parse_blog(feed_url: str = BLOG_FEED_URL, max_results: int = 500,
               clean_html: bool = True, extract_imgs: bool = True) -> dict:
    """
    Основная функция парсинга блога.

    Returns:
        dict с метаданными блога и списком постов
    """
    print(f'Загрузка feed: {feed_url}...')
    xml_data = fetch_feed(feed_url, max_results)

    print('Парсинг XML...')
    root = ET.fromstring(xml_data)

    # Метаданные блога
    blog_id_elem = root.find('atom:id', NAMESPACES)
    blog_title_elem = root.find('atom:title', NAMESPACES)
    blog_subtitle_elem = root.find('atom:subtitle', NAMESPACES)
    total_results = root.find('openSearch:totalResults', NAMESPACES)

    blog_info = {
        'blog_id': blog_id_elem.text if blog_id_elem is not None else None,
        'title': blog_title_elem.text if blog_title_elem is not None else None,
        'subtitle': blog_subtitle_elem.text if blog_subtitle_elem is not None else None,
        'total_posts': int(total_results.text) if total_results is not None else 0,
        'blog_url': BLOG_URL,
        'feed_url': feed_url,
        'parsed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
    }

    print(f'Найдено постов: {blog_info["total_posts"]}')

    # Парсинг постов
    entries = root.findall('atom:entry', NAMESPACES)
    posts = []

    for i, entry in enumerate(entries):
        try:
            post = parse_entry(entry, clean_html, extract_imgs)
            posts.append(post)
            print(f'  [{i + 1}/{len(entries)}] {post["title"][:60]}...')
        except Exception as e:
            print(f'  [{i + 1}/{len(entries)}] ОШИБКА: {e}')

    return {
        'blog': blog_info,
        'posts': posts,
    }


def parse_entry(entry, clean_html: bool, extract_imgs: bool) -> dict:
    """Парсит один entry из Atom feed."""

    # Базовые поля
    eid = _find_text(entry, 'atom:id')
    title = _find_text(entry, 'atom:title')
    published = _find_text(entry, 'atom:published')
    updated = _find_text(entry, 'atom:updated')

    # Контент
    content_elem = entry.find('atom:content', NAMESPACES)
    raw_html = content_elem.text if content_elem is not None else ''

    # Ссылка на пост
    link_elem = entry.find("atom:link[@rel='alternate']", NAMESPACES)
    url = link_elem.get('href') if link_elem is not None else ''

    # Категории/теги
    categories = [c.get('term') for c in entry.findall('atom:category', NAMESPACES) if c.get('term')]

    # Обработка HTML
    cleaned_html = clean_blogger_html(raw_html) if clean_html else html_module.unescape(raw_html)
    plain_text = html_to_plain_text(cleaned_html)

    # Изображения
    images = extract_images(cleaned_html) if extract_imgs else []
    thumbnail = images[0] if images else None

    # Slug
    slug = generate_slug(title)

    # Описание для мета-тегов
    description = generate_description(cleaned_html, title)

    # Дата в формате для сайта
    published_iso = parse_iso_date(published)
    updated_iso = parse_iso_date(updated)

    # Считаем слова
    word_count = count_words(plain_text)

    post = {
        'id': eid,
        'title': title.strip() if title else '',
        'slug': slug,
        'url': url,
        'original_url': url,
        'published': published_iso,
        'updated': updated_iso,
        'description': description,
        'categories': categories,
        'tags': categories,
        'author': {
            'name': 'SOCHIAUTOPARTS',
            'url': 'https://sochiautoparts.ru',
        },
        'content_html': cleaned_html,
        'content_text': plain_text,
        'thumbnail': thumbnail,
        'images': images,
        'image_count': len(images),
        'word_count': word_count,
        # SEO-поля для sochiautoparts.ru
        'seo': {
            'headline': title.strip() if title else '',
            'description': description,
            'inLanguage': 'ru-RU',
            'articleSection': 'Autos',
        },
        # JSON-LD блок для встраивания в сайт
        'json_ld': {
            '@context': 'https://schema.org',
            '@type': 'NewsArticle',
            'headline': title.strip() if title else '',
            'url': f'https://sochiautoparts.ru/blog/{slug}',
            'datePublished': published_iso,
            'dateModified': updated_iso,
            'description': description,
            'inLanguage': 'ru-RU',
            'publisher': {
                '@type': 'Organization',
                'name': 'SOCHIAUTOPARTS',
                'url': 'https://sochiautoparts.ru',
                'logo': {
                    '@type': 'ImageObject',
                    'url': 'https://raw.githubusercontent.com/creastudioai-beep/sap/main/main/assets/logo.jpg',
                }
            },
            'mainEntityOfPage': {
                '@type': 'WebPage',
                '@id': url,
            },
            'author': {
                '@type': 'Organization',
                'name': 'SOCHIAUTOPARTS',
            },
            'articleSection': 'Autos',
            'wordCount': word_count,
            'image': {
                '@type': 'ImageObject',
                'url': thumbnail,
            } if thumbnail else None,
        },
    }

    return post


def _find_text(element, xpath: str) -> str | None:
    """Безопасное получение текста из XML элемента."""
    el = element.find(xpath, NAMESPACES)
    return el.text if el is not None else None


# ─────────────────────────────────────────────────────────────
# Сохранение результатов
# ─────────────────────────────────────────────────────────────
def save_results(data: dict, output_path: str):
    """Сохраняет результат в JSON файл с красивым форматированием."""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'\nСохранено: {output_path}')
    print(f'  Размер файла: {_format_size(len(json.dumps(data, ensure_ascii=False, indent=2)))}')
    print(f'  Всего постов: {len(data["posts"])}')


def save_posts_only(data: dict, output_path: str):
    """Сохраняет только массив постов (удобно для импорта)."""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data['posts'], f, ensure_ascii=False, indent=2)
    print(f'Сохранено (только посты): {output_path}')


def save_summary(data: dict, output_path: str):
    """Сохраняет краткую сводку по постам (без контента)."""
    summary = {
        'blog': data['blog'],
        'posts': [
            {
                'title': p['title'],
                'slug': p['slug'],
                'url': p['url'],
                'published': p['published'],
                'updated': p['updated'],
                'description': p['description'],
                'categories': p['categories'],
                'thumbnail': p['thumbnail'],
                'image_count': p['image_count'],
                'word_count': p['word_count'],
                'json_ld': p['json_ld'],
            }
            for p in data['posts']
        ]
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f'Сохранена сводка: {output_path}')


def _format_size(bytes_size: int) -> str:
    for unit in ('B', 'KB', 'MB'):
        if bytes_size < 1024:
            return f'{bytes_size:.1f} {unit}'
        bytes_size /= 1024
    return f'{bytes_size:.1f} GB'


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Парсер блога sochi-autoparts.blogspot.com -> JSON для sochiautoparts.ru',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python3 blogger_parser.py
  python3 blogger_parser.py --output posts.json
  python3 blogger_parser.py --output posts.json --clean
  python3 blogger_parser.py --summary-only
        """
    )
    parser.add_argument('--output', '-o', default='blog_posts.json',
                        help='Имя выходного JSON файла (по умолчанию: blog_posts.json)')
    parser.add_argument('--posts-only', action='store_true',
                        help='Сохранить только массив постов без метаданных блога')
    parser.add_argument('--summary-only', action='store_true',
                        help='Сохранить краткую сводку без HTML контента')
    parser.add_argument('--no-clean', action='store_true',
                        help='Не очищать HTML от Blogger-мусора')
    parser.add_argument('--no-images', action='store_true',
                        help='Не извлекать изображения')
    parser.add_argument('--max-results', type=int, default=500,
                        help='Максимальное количество постов (по умолчанию: 500)')
    parser.add_argument('--feed-url', default=BLOG_FEED_URL,
                        help='URL Atom feed блога')

    args = parser.parse_args()

    print('=' * 60)
    print('Парсер блога sochi-autoparts.blogspot.com')
    print('=' * 60)

    data = parse_blog(
        feed_url=args.feed_url,
        max_results=args.max_results,
        clean_html=not args.no_clean,
        extract_imgs=not args.no_images,
    )

    # Статистика
    print(f'\n--- Статистика ---')
    total_words = sum(p['word_count'] for p in data['posts'])
    total_images = sum(p['image_count'] for p in data['posts'])
    print(f'Всего постов: {len(data["posts"])}')
    print(f'Всего слов: {total_words:,}')
    print(f'Всего изображений: {total_images:,}')

    # Сохранение
    if args.summary_only:
        base = args.output.replace('.json', '')
        save_summary(data, f'{base}_summary.json')
    elif args.posts_only:
        save_posts_only(data, args.output)
    else:
        save_results(data, args.output)
        # Дополнительно сохраняем сводку
        base = args.output.replace('.json', '')
        save_summary(data, f'{base}_summary.json')

    print('\nГотово!')


if __name__ == '__main__':
    main()
