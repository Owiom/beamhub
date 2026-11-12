# Как выложить BeamHub на GitHub

Git уже установлен. Всё остальное — три шага.

## 1. Создать репозиторий

На github.com: **New repository** → имя `beamhub` → Public → **Create**.
README и .gitignore не добавляйте — они уже есть.

## 2. Запустить `publish.bat`

Двойной клик, вставить ссылку на репозиторий:

```
https://github.com/ВАШ_НИК/beamhub.git
```

Скрипт сам: настроит автора коммита, сделает первый коммит, добавит remote,
запушит `main`, поставит тег `v0.7.1` и запушит его.

При первом пуше откроется окно входа в GitHub — это единственное, что нужно
сделать руками (логин/пароль или вход через браузер).

## 3. Дождаться релиза

Тег `v0.7.1` запускает `.github/workflows/release.yml`: он соберёт exe на
`windows-latest` и прикрепит `BeamHub-v0.7.1-win64-portable.zip` к релизу.

После этого в настройках BeamHub впишите репозиторий (`ВАШ_НИК/beamhub`) —
и автообновление начнёт находить новые версии.

## Вручную (если скрипт не подошёл)

```bat
cd beamhub
git init
git add -A
git commit -m "BeamHub 0.7.1"
git branch -M main
git remote add origin https://github.com/ВАШ_НИК/beamhub.git
git push -u origin main
git tag v0.7.1
git push origin v0.7.1
```

## Что попадёт в репозиторий

Код, интерфейс, провайдеры, тесты, workflow, README и SECURITY.
Не попадёт (см. `.gitignore`): `data/` (база и кеш), `seed/` (готовый каталог
для сборки), `dist/`, `build/`, `*.zip` — это артефакты.

## Следующие версии

```bat
:: поправить VERSION в core/version.py, закоммитить, затем
git tag v0.7.2
git push origin v0.7.2
```

Каждый тег = новый релиз = новое автообновление у пользователей.
