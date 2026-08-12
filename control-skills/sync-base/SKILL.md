---
name: sync-base
description: Проверить и установить стабильный OpenCode-base release по одностороннему каналу.
compatibility: opencode
---

# sync-base

Запускай только по явному запросу пользователя.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "$HOME\.config\opencode\skills\sync-base\tools\sync_base.ps1" `
  -PolicyPath "$HOME\.config\opencode\skills\sync-base\sync-policy.json"
```

1. Выбери последний стабильный `opencode-vX.Y.Z`.
2. Скачай пакет и release manifest.
3. Сверь target, версию и SHA-256 пакета из manifest.
4. Один раз передай пакет Foundation для `install`: эта команда сама проверяет
   пакет и создаёт резервную копию.
5. Используй сохранённый профиль Direct, VPN, HTTP, HTTPS или SOCKS5; секрет
   не выводи в лог или историю команд.

Нельзя устанавливать `gh`, выполнять login, брать prerelease или понижать
версию. Consumer ничего не отправляет на hub.
