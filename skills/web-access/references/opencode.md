# web-access — OpenCode

Детерминированная часть скилла — общий код для обеих сред, без изменений;
меняется только то, что берётся после её исчерпания.

## Что общее

Первый шаг любого «сходи в сеть» — та же команда, тот же файл:

```
python ~/.agents/skills/web-access/tools/web_get.py <URL> [--kind page|file|auto] [-o out] [--json]
```

Ступени (direct/noproxy/jina/ru), верификация ответа (не HTTP 200, а факт —
непустая страница без challenge-маркера, файл с верной сигнатурой) и реестр
главных сайтов `tools/main_sites.md` не зависят от того, какая LLM вызывает
скрипт — это чистый Python-процесс без обращений к MCP. RU-слой
(`ru-gov-access/tools/ru_fetch.py`, RU exit-IP для госсайтов) тоже общий,
`web_get` вызывает его одинаково в обеих средах.

## Что меняется — ступень после web_get

Когда кодовые ступени исчерпаны (`web_get` вернул `ok:false` и напечатал
`next`), у OpenCode следующий шаг — MCP exa/firecrawl/playwright. У OpenCode этих
серверов нет: exa исключён из `opencode-layer/mcp-whitelist.json` 2026-07-14
(свой веб-поиск считается достаточной заменой), firecrawl и playwright в
белый список OpenCode не входили.

| Ступень после web_get | OpenCode | OpenCode |
|---|---|---|
| Семантический поиск/чтение | exa (`web_search_exa`/`web_fetch_exa`) | встроенный веб-поиск OpenCode |
| Скрейп с извлечением | firecrawl | встроенный веб-поиск OpenCode (отдельного скрейпера нет) |
| JS/SPA, антибот, cookies | playwright | `browser:control-in-app-browser` или `chrome:control-chrome` |
| Сомнение «сайта/документа нет» — смотреть глазами (ШАГ 0) | `playwright browser_take_screenshot` | скриншот тем же браузер-плагином |

Между двумя браузер-плагинами OpenCode: `control-in-app-browser` подходит,
когда достаточно окна внутри приложения; `control-chrome` — когда нужен
профиль реального Chrome пользователя (сохранённые cookies, антибот,
тяжёлые SPA) — тот же случай, для которого у OpenCode был playwright с
прокси на RU exit-IP.

## Анти-капитуляция

Правило не меняется: вывод «не нашёл» требует, чтобы главные источники
категории из `tools/main_sites.md` были пройдены и список проверенного
приложен к ответу. Падение одной ступени — кодовой или браузер-плагина —
повод перейти к следующей, не повод завершать поиск.

## Соответствие понятий

| Наше понятие | OpenCode | OpenCode |
|---|---|---|
| Детерминированная лестница | `tools/web_get.py` | тот же файл без изменений |
| MCP семантический поиск | exa | встроенный веб-поиск |
| MCP скрейп | firecrawl | встроенный веб-поиск (без отдельного инструмента) |
| MCP браузер (JS/антибот/cookies) | playwright | `browser:control-in-app-browser` / `chrome:control-chrome` |
| RU exit-IP слой | ru-gov-access | тот же скрипт, без изменений |
| Реестр главных сайтов | `tools/main_sites.md` | тот же файл |

## Связи

- Основной `SKILL.md` — детерминированные ступени, верификация, гео-модель,
  без изменений.
- `~/.config/opencode/base/opencode-layer/mcp-whitelist.json` — состав MCP, доступных OpenCode
  (сейчас `time`; список предварительный до тест-заезда Задачи 11).
- [[ru-gov-access]] — RU-слой, общий для OpenCode и OpenCode.
