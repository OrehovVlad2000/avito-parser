"""
Парсер объявлений Авито.

Описание:
    Скрипт автоматически собирает объявления с сайта Авито по заданному поисковому запросу,
    сохраняет данные в базу SQLite и экспортирует в Excel.

Особенности:
    - Обход базовой защиты Авито через Playwright (эмуляция браузера);
    - Экспорт результатов в формат Excel (.xlsx);
    - Поддержка пауз для имитации поведения человека.

Регион поиска: Новосибирск
Технологии:
    - Python 3.12.10
    - Playwright 1.58.0,
    - BeautifulSoup 4.14.3,
    - SQLite,
    - Pandas 3.0.2
"""

#--------------------------------------------------------------------------------------------
# Импорт библиотек
#--------------------------------------------------------------------------------------------

# Playwright — управление браузером Chrome (обход защиты сайтов)
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# BeautifulSoup — парсинг HTML-кода (извлечение данных из страницы)
from bs4 import BeautifulSoup

# SQLite — локальная база данных для кэширования объявлений
import sqlite3

# Pandas — работа с табличными данными и экспорт в Excel
import pandas as pd

# Стандартные библиотеки для работы со временем, текстом и файлами
import time
import random
import re
from datetime import datetime, timedelta

#--------------------------------------------------------------------------------------------
# Задаем константы и настройки
#--------------------------------------------------------------------------------------------

# Путь к файлу базы данных (создаётся автоматически в папке проекта)
DB_PATH = "avito_cache.db"

# Шаблон URL для поиска объявлений
# {query} — поисковый запрос (например, "монеты")
# {page} — номер страницы выдачи (1, 2, 3...)
# Регион: novosibirsk (можно заменить на любой другой)
URL = "https://www.avito.ru/novosibirsk?q={query}&p={page}"

# Максимальное количество страниц для парсинга
MAX_PAGES = 1

# Таймаут ожидания загрузки страницы (в миллисекундах)
# Если страница не загрузится за 30 сек — будет ошибка
TIMEOUT_MS = 30000


#--------------------------------------------------------------------------------------------
# Функции для работы с БД (SQLite)
#--------------------------------------------------------------------------------------------

def init_db():
    """
    Инициализация базы данных.

    Создаёт таблицу ads, если она ещё не существует.
    Таблица хранит все данные объявлений + мета-информацию для кэширования.
    """

    # Подключаемся к файлу БД (создаётся автоматически при первом запуске)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # SQL-запрос на создание таблицы
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            ad_id TEXT PRIMARY KEY,
            query TEXT, 
            title TEXT, 
            price TEXT, 
            address TEXT,
            description TEXT, 
            date_published TEXT, 
            views TEXT,
            link TEXT, 
            status TEXT, 
            created_at TEXT, 
            updated_at TEXT
        )
    """)

    # Сохраняем изменения и закрываем соединение
    conn.commit()
    conn.close()


def save_ad(cursor, ad, query, is_update=False):
    """
    Сохранение объявления в базу данных.

    Параметры:
        cursor — курсор базы данных
        ad — словарь с данными объявления
        query — поисковый запрос
        is_update — True если обновляем существующую запись, False если добавляем новую
    """

    # Текущее время для записи в created_at или updated_at
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if is_update:
        # Обновляем существующую запись (меняем цену, статус, дату обновления)
        cursor.execute("""
            UPDATE ads SET title=?, price=?, address=?, description=?,
            date_published=?, views=?, status=?, updated_at=?
            WHERE ad_id=?
        """, (ad['title'], ad['price'], ad['address'], ad['description'],
              ad['date'], ad['views'], ad['status'], now, ad['ad_id']))
    else:
        # Добавляем новую запись (устанавливаем created_at и updated_at в текущее время)
        cursor.execute("""
            INSERT INTO ads VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ad['ad_id'], query, ad['title'], ad['price'], ad['address'],
              ad['description'], ad['date'], ad['views'], ad['link'],
              ad['status'], now, now))


def get_ads_from_db(query):
    """
   Получение всех объявлений из базы по поисковому запросу.

   Используется для экспорта данных в Excel после завершения парсинга.

   Возвращает:
       Список словарей с данными объявлений
   """

    # Подключаемся к БД с row_factory для доступа к полям по имени
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Выбираем все объявления для заданного запроса
    ads = [dict(row) for row in conn.execute("SELECT * FROM ads WHERE query=?", (query,))]

    conn.close()
    return ads


#--------------------------------------------------------------------------------------------
# Функции для парсинга (страница поиска)
#--------------------------------------------------------------------------------------------

def parse_search_page(page):
    """
    Извлекает базовую информацию со страницы поиска Авито.

    Параметры:
        page — объект страницы Playwright

    Возвращает:
        Список словарей с базовой информацией (ID, заголовок, ссылка)
    """
    ads = []

    # Шаг 1: Ожидание загрузки карточек объявлений
    try:
        # Ждём появления элементов с атрибутом data-marker="item"
        page.wait_for_selector('[data-marker="item"]', timeout=20000)
        print("  ✅ Карточки загружены")
    except PlaywrightTimeout:
        # Если карточки не появились за 20 секунд — возможно проблема с загрузкой
        print("  ❌ Карточки не найдены за 20 сек")
        return []

    # Небольшая пауза для полной подгрузки страницы
    time.sleep(2)

    # Шаг 2: Получение HTML-кода страницы
    try:
        # evaluate выполняет JavaScript в браузере и возвращает весь HTML
        html = page.evaluate("document.documentElement.outerHTML")
    except:
        # Если не получилось с первого раза — пробуем ещё раз через секунду
        print(f"  ❌ Не удалось получить HTML: {e}")
        time.sleep(1)
        html = page.evaluate("document.documentElement.outerHTML")

    # Шаг 3: Парсинг HTML через BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')

    # Находим все карточки объявлений по атрибуту data-marker="item"
    items = soup.find_all('div', {'data-marker': 'item'})
    print(f"  🔍 Найдено элементов: {len(items)}")

    # Шаг 4: Извлечение данных из каждой карточки
    for item in items:
        try:
            # 🔍 Ссылка на объявление
            # Ищем ссылку с атрибутом data-marker="item-title"
            link_tag = item.find('a', {'data-marker': 'item-title'})

            # Если не нашли — пробуем любую ссылку внутри карточки
            if not link_tag:
                link_tag = item.find('a', href=True)

            # Если ссылки нет вообще — пропускаем эту карточку
            if not link_tag:
                continue

            href = link_tag.get('href')

            # 🔍 ID объявления
            # Извлекаем числовой идентификатор из URL (7+ цифр в конце)
            match = re.search(r'(\d{7,})', href)

            # Нет ID — пропускаем
            if not match:
                continue
            ad_id = match.group(1)

            # 🔍 Заголовок объявления
            # Приоритет: атрибут title > текст ссылки > "Без названия"
            title = link_tag.get('title') or link_tag.get_text(strip=True) or "Без названия"

            # Полная ссылка
            full_link = href if href.startswith('http') else f"https://www.avito.ru{href}"

            # (ID, заголовок, ссылка)
            ads.append({
                "ad_id": ad_id,
                "title": title,
                "link": full_link
                # Остальное заполнится в parse_ad_page()
            })
        except:
            # Если ошибка при обработке одной карточки — пропускаем её, продолжаем дальше
            continue

    # Шаг 5: Удаление дубликатов
    # Авито может показывать одни и те же объявления в разных местах выдачи
    seen = set()
    unique = []
    for ad in ads:
        if ad["ad_id"] not in seen:
            seen.add(ad["ad_id"])
            unique.append(ad)

    print(f"  🔍 Найдено: {len(unique)} уникальных объявлений")
    return unique


#--------------------------------------------------------------------------------------------
# Функции для парсинга (страница объявления)
#--------------------------------------------------------------------------------------------

def parse_ad_page(page):
    """
    Извлечение подробной информации со страницы конкретного объявления.

    Параметры:
        page — объект страницы Playwright (открытое объявление)

    Возвращает:
        Словарь с полными данными (заголовок, цена, адрес, описание, дата, просмотры)
    """

    # Получаем HTML-код текущей страницы
    soup = BeautifulSoup(page.content(), 'html.parser')

    # Инициализируем словарь с данными (заполняем по мере извлечения)
    data = {
        "title": "",
        "price": "",
        "address": "",
        "description": "",
        "date": "",
        "views": "",
        "status": "active" # По умолчанию считаем активным
    }

    # 🔍 Заголовок

    # Основной селектор: h1 с data-marker="item-view/title-info"
    h1 = soup.find('h1', {'data-marker': 'item-view/title-info'})

    # Резервный селектор (если основной не сработал)
    # Пробуем найти любой тег h1 на странице
    if not h1:
        h1 = soup.find('h1')

    # Извлекаем текст из заголовка, убираем лишние пробелы
    # Если h1 не найден — ставим "Без названия"
    data["title"] = h1.get_text(strip=True) if h1 else "Без названия"

    # 🔍 Цена

    # Приоритет 1: span с data-marker="item-view/item-price"
    price_tag = soup.find('span', {'data-marker': 'item-view/item-price'})

    # Приоритет 2: span с itemprop="price"
    if not price_tag:
        price_tag = soup.find('span', {'itemprop': 'price'})

    # Приоритет 3: meta с itemprop="price"
    if not price_tag:
        price_tag = soup.find('meta', {'itemprop': 'price'})

    # Извлекаем цену из найденного элемента
    if price_tag:
        # Если есть атрибут content — берём оттуда (чистое число)
        if price_tag.get('content'):
            data["price"] = price_tag.get('content') + ' ₽'
        # Иначе берём текст из тега
        else:
            data["price"] = price_tag.get_text(strip=True)
    else:
        data["price"] = "Не указана"

    # 🔍 Адрес

    # Способ 1: Ищем по data-marker (оригинальный селектор)
    addr_container = soup.find('div', {'data-marker': 'item-view/item-address'})

    # Способ 2: Ищем по itemprop="address"
    if not addr_container:
        addr_container = soup.find('div', {'itemprop': 'address'})

    # Если нашли контейнер адреса — извлекаем текст из внутреннего span
    if addr_container:
        # Ищем span внутри контейнера (там обычно город/район)
        addr_span = addr_container.find('span')
        if addr_span:
            address_text = addr_span.get_text(strip=True)
            # Проверяем, что текст не пустой (иногда span есть, но без текста)
            if address_text:
                data["address"] = address_text

    # Если адрес всё ещё не найден — ставим значение по умолчанию
    if not data["address"]:
        data["address"] = "Адрес не указан"

    # 🔍 Описание

    # Основной селектор: div с data-marker="item-view/item-description"
    desc_tag = soup.find('div', {'data-marker': 'item-view/item-description'})

    # Резервный селектор: div с itemprop="description"
    if not desc_tag:
        desc_tag = soup.find('div', {'itemprop': 'description'})

    # Извлекаем текст описания
    if desc_tag:
        # [:2000] ограничивает длину (чтобы не занимало слишком много в БД)
        data["description"] = desc_tag.get_text(strip=True)[:2000]
    else:
        data["description"] = ""

    # 🔍 Дата

    # Ищем span с data-marker="item-view/item-date"
    date_tag = soup.find('span', {'data-marker': 'item-view/item-date'})

    if date_tag:
        # Преобразуем в стандартный формат через normalize_date()
        data["date"] = normalize_date(date_tag.get_text(strip=True))
    else:
        # Если дата не найдена — ставим текущее время
        data["date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 🔍 Просмотры

    # Ищем span с data-marker="item-view/total-views"
    views_tag = soup.find('span', {'data-marker': 'item-view/total-views'})
    if views_tag:
        data["views"] = views_tag.get_text(strip=True)
    else:
        data["views"] = ""

    # 🔍 Статус

    # Ищем специальный маркер закрытого объявления
    # Авито добавляет data-marker="item-view/closed-warning" только к снятым объявлениям
    # Если маркер найден — объявление закрыто, иначе считаем активным
    closed_warning = soup.find('div', {'data-marker': 'item-view/closed-warning'})

    data["status"] = "closed" if closed_warning else "active"

    return data


#--------------------------------------------------------------------------------------------
# Преобразование даты
#--------------------------------------------------------------------------------------------

def normalize_date(date_text):
    """
    Преобразование даты из формата Авито в стандартный формат БД.

    Входные форматы:
        • "сегодня в 14:30"
        • "вчера в 10:00"
        • "3 дня назад"
        • "24 марта в 23:20"

    Выходной формат:
        "YYYY-MM-DD HH:MM:SS"

    Параметры:
        date_text — строка с датой из объявления

    Возвращает:
        Строка с датой в стандартном формате
    """

    # Если дата пустая — возвращаем текущее время
    if not date_text:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    now = datetime.now()
    text = date_text.lower().strip()

    # Удаляем лишние символы в начале (·, -, пробелы)
    text = re.sub(r'^[\s·-]+', '', text)

    # Сегодня
    if "сегодня" in text:
        time_match = re.search(r'(\d{1,2}):(\d{2})', text)
        if time_match:
            # Есть время: "сегодня в 14:30" → "YYYY-MM-DD 14:30:00"
            return now.strftime("%Y-%m-%d") + f" {time_match.group(1)}:{time_match.group(2)}:00"
        # Без времени: "сегодня" → "YYY-MM-DD 00:00:00"
        return now.strftime("%Y-%m-%d %H:%M:%S")

    # Вчера
    if "вчера" in text:
        yesterday = now - timedelta(days=1)
        time_match = re.search(r'(\d{1,2}):(\d{2})', text)
        if time_match:
            return yesterday.strftime("%Y-%m-%d") + f" {time_match.group(1)}:{time_match.group(2)}:00"
        return yesterday.strftime("%Y-%m-%d %H:%M:%S")

    # Дней назад
    days_match = re.search(r'(\d+)\s*дн', text)
    if days_match:
        days = int(days_match.group(1))
        return (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    # Полная дата (например, "24 марта в 23:20")
    full_match = re.search(r'(\d{1,2})\s+(\w+)\s+(?:в\s+)?(\d{1,2}):(\d{2})', text)
    if full_match:
        day = int(full_match.group(1))
        month_name = full_match.group(2)
        hour = int(full_match.group(3))
        minute = int(full_match.group(4))

        # Словарь для преобразования названия месяца в номер
        months = {
            "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
            "мая": 5, "июня": 6, "июля": 7, "августа": 8,
            "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12
        }
        month = months.get(month_name, 1)

        try:
            # Создаём дату с указанием года
            date = datetime(now.year, month, day, hour, minute, 0)
            # Если дата получилась в будущем (например, январь при текущем декабре), значит это был прошлый год
            if date > now:
                date = datetime(now.year - 1, month, day, hour, minute, 0)
            return date.strftime("%Y-%m-%d %H:%M:%S")
        except:
            pass

    # Если не удалось распарсить — возвращаем текущее время
    return now.strftime("%Y-%m-%d %H:%M:%S")


#--------------------------------------------------------------------------------------------
# ОСНОВНОЙ ПРОЦЕСС
#--------------------------------------------------------------------------------------------

def main():
    """
    Главная функция программы.

    Последовательность действий:
        1. Инициализация базы данных
        2. Запуск браузера Playwright
        3. Поочерёдный парсинг страниц поиска
        4. Для каждого объявления — переход на страницу и сбор деталей
        5. Сохранение/обновление в БД
        6. Экспорт результатов в Excel
    """

    # Вывод заголовка программы
    print("-" * 60)
    print("Парсер Авито")
    print("-" * 60)

    query = input("\nВведите поисковый запрос (например, монеты): ").strip()

    # Проверка на пустой запрос
    if not query:
        print("❌ Поисковый запрос не может быть пустым!")
        return

    # Инициализация базы данных (создаёт таблицу, если нет)
    init_db()

    # Счётчики статистики для отчёта в конце
    stats = {"new": 0, "updated": 0, "errors": 0}

    # Запуск браузера
    # with sync_playwright() гарантирует корректное закрытие браузера даже при ошибке
    with sync_playwright() as p:
        print("\n[1/2] Запуск браузера...")

        # Запускаем Chromium в видимом режиме (headless=False для отладки)
        # Для скрытого режима нужно заменить на headless=True
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized"] # Развернуть на весь экран
        )

        # Создаём контекст браузера с настройками
        context = browser.new_context(
            # User-Agent как у обычного пользователя (маскировка под браузер)
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            # Размер окна 1920x1080 (стандартный Full HD)
            viewport={"width": 1920, "height": 1080}
        )

        # Открываем новую страницу
        page = context.new_page()

        # Скрываем признаки автоматизации
        # Авито может проверять navigator.webdriver для детекции ботов
        # Этот скрипт делает свойство webdriver невидимым
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        print("[2/2] Начинаем парсинг...\n")

        # Цикл по страницам поиска
        try:
            for page_num in range(1, MAX_PAGES + 1):
                print(f"Страница {page_num}/{MAX_PAGES}")

                # Формируем URL для текущей страницы
                url = URL.format(query=query.replace(" ", "+"), page=page_num)

                # Переходим на страницу поиска
                try:
                    # wait_until="domcontentloaded" — ждём загрузки DOM, но не всех картинок
                    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                except PlaywrightTimeout:
                    print("  ❌ Таймаут загрузки страницы")
                    stats["errors"] += 1
                    continue

                # Пауза для имитации поведения человека (случайное время 2-4 сек)
                time.sleep(random.uniform(2, 4))

                # ПРОВЕРКА НА CAPTCHA
                # Если Авито показал капчу — URL будет содержать "captcha"
                if "captcha" in page.url.lower():
                    print("\n⚠️ Обнаружена CAPTCHA!")
                    print("  🛠️ Решите её вручную в браузере и нажмите Enter...")
                    input("    [Ожидание...]")
                    time.sleep(2) # Пауза после решения

                # Парсим страницу поиска (получаем список объявлений)
                ads = parse_search_page(page)

                # Цикл по каждому объявлению
                for ad_info in ads:
                    try:
                        # Открываем объявление в новой вкладке
                        # Это позволяет быстро вернуться к поиску после парсинга
                        new_page = context.new_page()
                        new_page.goto(ad_info["link"], wait_until="domcontentloaded", timeout=TIMEOUT_MS)

                        # Пауза для загрузки контента объявления
                        time.sleep(random.uniform(2, 4))

                        # Парсим страницу объявления (получаем детали)
                        extra = parse_ad_page(new_page)

                        # Добавляем детали к основной информации
                        ad_info.update(extra)

                        # Закрываем вкладку с объявлением (освобождаем память)
                        new_page.close()

                        # Работа с БД
                        conn = sqlite3.connect(DB_PATH)
                        conn.row_factory = sqlite3.Row

                        # Проверяем, есть ли уже это объявление в кэше
                        existing = conn.execute("SELECT * FROM ads WHERE ad_id=?", (ad_info["ad_id"],)).fetchone()

                        if not existing:
                            # Объявления нет в БД — добавляем новое
                            if ad_info["status"] == "active":
                                save_ad(conn.cursor(), ad_info, query, is_update=False)
                                stats["new"] += 1
                                print(f"  Добавлено: {ad_info['title'][:50]}")
                        # Объявление уже есть — проверяем изменения
                        # Обновляем, если изменилась цена (основной индикатор)
                        elif existing["price"] != ad_info["price"]:
                            save_ad(conn.cursor(), ad_info, query, is_update=True)
                            stats["updated"] += 1
                            print(f"  Обновлено: {ad_info['title'][:50]}")

                        # Сохраняем изменения в БД и закрываем соединение
                        conn.commit()
                        conn.close()

                    except Exception as e:
                        # Ошибка при обработке одного объявления — не прерываем весь парсинг
                        print(f"  ❌ Ошибка: {e}")
                        stats["errors"] += 1
                        continue

                # Пауза между страницами поиска (имитация человека)
                if page_num < MAX_PAGES:
                    print("  Пауза перед следующей страницей...")
                    time.sleep(random.uniform(4, 7))

        # Обработка прерывания пользователем
        except KeyboardInterrupt:
            print("\n⚠️ Парсинг прерван пользователем")

        # Гарантированное закрытие браузера (даже при ошибке)
        finally:
            browser.close()

    # Запись в Excel
    # Получаем все сохранённые объявления из БД
    ads = get_ads_from_db(query)

    if ads:
        # Создаём DataFrame из списка словарей
        df = pd.DataFrame(ads)

        # Формируем имя файла с датой и временем
        filename = f"avito_{query}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"

        # Сохраняем в Excel (без индекса строк)
        df.to_excel(filename, index=False)

        print(f"\n✅ Экспорт: {filename} ({len(ads)} записей)")

    else:
        print("\n⚠️ Нет данных для экспорта")

    print(f"\n✅ Готово! Новых: {stats['new']}, Обновлено: {stats['updated']}, Ошибок: {stats['errors']}")

# Запуск
if __name__ == "__main__":
    main()