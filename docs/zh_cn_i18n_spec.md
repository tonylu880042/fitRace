# Simplified Chinese (zh-CN) + all hub pages on the locale API — spec (2026-10-16)

## Why
FitRace may run events in mainland China, so every hub page needs a Simplified
Chinese version. Pages that read `hub_server/infrastructure/locales/*.json`
via `/api/locales` (dashboard index.html, classAdmin, result, results, teams)
only need a new locale file. Four pages keep their own inline zh/en strings
and have to move onto the locale files first: gameAdmin.html (~568 inline
keys), systemAdmin.html (~286), signup.html (~118), docs.html (lang-en /
lang-zh spans). This also clears the existing CLAUDE.md i18n violation
("never hardcode zh/en strings in static pages").

Out of scope: edge node pages (edge_node/…, EDGE_SETUP_HTML, operator.html),
cloud_signup/, admin.html, simulator.html. Do not touch them.

## 1. Locale infrastructure — `hub_server/infrastructure/locales.py`
- `SUPPORTED_LOCALES` gains `"zh-CN"`, label `"简体中文"`.
- Aliases: `zh-cn`, `zh-hans`, `zh-sg`, `zh-hans-cn` → `zh-CN`. Keep
  `zh`, `zh-tw` → `zh-TW`; add `zh-hk`, `zh-hant` → `zh-TW`.
- Completeness policy: **en-US, zh-TW and zh-CN must have exactly the same
  key set** (they are the complete locales). it, fr, de-CH and sv may be
  missing keys: `load_locale` returns en-US merged with that locale (the
  locale's value wins), so a missing key shows English, which is what those
  users see today on the inline-dict pages. A non-en locale must never have
  a key en-US lacks. Update `tests/unit/hub/test_i18n_locales.py`
  `test_all_supported_locales_have_matching_keys` to this rule; keep every
  other existing locale test.
- New keys added by this work go into en-US, zh-TW and zh-CN only. Do not
  delete existing translations from the other four locales.

## 2. zh-CN.json
- Generate from zh-TW.json with OpenCC config `tw2sp` (Taiwan → Simplified
  with mainland phrasing). OpenCC is a ONE-TIME tool: install it in a
  throwaway venv in your scratch dir, never add it to pyproject or the
  repo. Commit the generated JSON, not a generator script.
- Then hand-review for mainland usage, e.g. 設定→设置, 網路→网络,
  影片→视频, 資料→数据, 列印→打印, 預設→默认, 登入→登录, 伺服器→服务器,
  訊息→信息/消息, 螢幕→屏幕, 程式→程序, 支援→支持, 檔案→文件.
  Keep placeholders (`{station}` etc.) byte-identical to zh-TW.
- Test: every zh-CN value has the same placeholders as en-US for that key,
  and no zh-CN value contains any of a fixed list of ~40 common
  Traditional-only characters (體 設 備 隊 賽 號 開 終 選 與 為 這 們 時 點
  機 網 線 經 進 發 長 動 應 該 電 會 區 數 據 讀 認 證 錄 單 門 間 問 題 頁 顯
  標 準 幕). Assert on the loaded JSON, not on source text.

## 3. Backend zh-specific branches
- `_resolve_export_lang` (results CSV export) and the roster template
  endpoint (`resolved_lang = "zh-TW" if lang != "en" else "en"`): accept
  `zh-CN` and emit Simplified headers/labels for it.
- Roster CSV import (`hub_server/usecases/roster.py`): header aliases must
  also accept Simplified 队伍, 队名, 组别, 性别 and values 男子组, 女子组
  (男/女/男子/女子 already match). Test a Simplified CSV imports with team
  and division populated.
- Grep the hub for other `zh-TW`-only branches; give each a zh-CN path or
  report why not.

## 4. Migrate the four pages to the locale API (one commit per page)
For each page:
- Move every user-visible string into the locale JSON files under a page
  prefix (`game_admin.`, `system_admin.`, `signup_page.`, `docs.`) so no key
  collides with existing keys. en-US and zh-TW values are moved verbatim
  from the page's inline dictionaries / spans; zh-CN via §2 conversion +
  review.
- The page loads `/api/locales` + `/api/locales/{locale}` exactly like
  classAdmin.html / teams.html, shows a language `<select>` built from
  `/api/locales` (replacing EN/中文 toggle buttons), remembers the choice in
  the same `fitrace.locale` localStorage key, and falls back from the
  browser language (zh-CN/zh-Hans → zh-CN, zh-TW/zh-HK/zh → zh-TW, else
  en-US). Until the fetch completes, or if it fails, show en-US text from
  a minimal built-in fallback ONLY if the page cannot render otherwise —
  prefer rendering the key-less English already in the HTML.
- Delete the inline dictionaries once migrated. Game Admin's
  `loadReadinessMessages` / `mergeReadinessMessages` merge hack becomes
  unnecessary — remove it.
- docs.html: replace each lang-en / lang-zh pair with one element carrying
  `data-i18n="docs.<key>"`. Values that need inline markup (links, <code>,
  <b>, lists) may use `data-i18n-html`; only for docs.html, and only
  because the values come from our own locale files.
- signup.html is athlete-facing: verify the whole sign-up flow text,
  validation messages and success screen are localized.

## 5. Tests — protect behaviour, don't weaken it
- Many existing page tests read the inline dictionaries. When you change
  one, it must still assert the same user-visible behaviour (e.g. the zh-TW
  text a label renders), now via the locale JSON. List every modified or
  deleted existing test in your report with one line on why.
- Per migrated page, add a test that runs the page's render path under
  `node` with the zh-CN locale messages and asserts representative strings
  render in Simplified Chinese, plus a test that no CJK string literal
  remains in the page's script/HTML (strip comments first; the language
  option labels come from the API so need no literals).
- Known trap: a test satisfied by a nearby comment containing the same
  substring. Strip comments before any source-text assertion.

## 6. Docs
- CLAUDE.md "i18n" section: replace the stale `(zh_tw.json, en.json)` with
  the real file list and state the completeness policy from §1.
