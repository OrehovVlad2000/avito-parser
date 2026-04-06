# Avito Parser

Автоматический парсер объявлений с сайта Avito с сохранением данных в SQLite и экспортом в Excel.

![Python](https://img.shields.io/badge/Python-3.12.10-3776AB.svg?logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-1.58.0-F56B00.svg)
![BeautifulSoup](https://img.shields.io/badge/BeautifulSoup-4.14.3-7B68EE.svg?logo=beautifulsoup&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-3.0.2-E70488.svg?logo=pandas&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-3.49.1-006400.svg?logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-4B0082.svg)

## Описание

Скрипт автоматически собирает объявления с Avito по заданному поисковому запросу, сохраняет данные в локальную базу SQLite и экспортирует результаты в формат Excel.

**Особенности:**
- ✅ Обход базовой защиты Avito через Playwright (эмуляция браузера)
- ✅ Кэширование объявлений с отслеживанием изменений (цена)
- ✅ Экспорт результатов в формат Excel (.xlsx)
- ✅ Поддержка пауз для имитации поведения человека
