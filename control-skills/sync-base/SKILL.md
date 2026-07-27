---
name: sync-base
description: Проверить и установить стабильный OpenCode-base release по одностороннему каналу.
compatibility: opencode
---

# sync-base

Запускай только по явному запросу пользователя.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "$HOME\.config\opencode\skills\sync-base\tools\sync_base.ps1"
```

1. Выбери последний стабильный `opencode-vX.Y.Z`.
2. Проверь immutable release и attestation каждого asset официальным GitHub
   verifier.
3. Сверь manifest, component inventory и SHA-256.
4. Передай пакет Foundation для `plan`, `install`, `doctor`.
5. Если install прошёл, а doctor нет, выполни Foundation rollback.
6. Используй сохранённый профиль Direct, VPN, HTTP, HTTPS или SOCKS5; секрет
   не выводи в лог или историю команд.

Нельзя устанавливать verifier, выполнять login, брать prerelease, понижать
версию или обходить проверку. Consumer ничего не отправляет на hub.
