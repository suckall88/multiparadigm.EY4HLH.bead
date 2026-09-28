# Edzésnapló

Python-beadandó — Multi paradigmás programozási nyelvek gyakorlat (Eszterházy Károly Katolikus Egyetem, 2026/2027 I. félév).

## Tartalomjegyzék

- [Alkalmazás](#alkalmazás)
- [Használat](#használat)
- [Karbantartás](#karbantartás)
- [Ellenőrzések](#ellenőrzések)
- [Források és bővítések](#források-és-bővítések)
- [Tervezési döntések](#tervezési-döntések)

## Alkalmazás

### Cél és fő funkciók

Az alkalmazás egy edzésnapló, amely rögzíti az edzéseket és az egyes gyakorlatokban elért szetteket (súly, ismétlésszám), majd ezekből **progressziót számol és mutat be** (fejlődik/plafonon van/visszaesik).

### Feldolgozási szabály (témaspecifikus)

**Progresszió-számítás** (`backend/domain/progression.py`): az Epley-formulával becsült egy-ismétléses maximum (e1RM) alapján, gyakorlatonként, a legjobb szettet véve minden edzésnapon. Legalább 3, a visszatekintési időszakon (alapértelmezetten 8 hét) belüli edzésnap szükséges, különben az eredmény `insufficient_data`. A trendet lineáris regresszióval (heti meredekség) számítjuk, és az átlagos e1RM ±0,5%-ához viszonyítva soroljuk `improving`/`plateau`/`declining` kategóriába.

### Adatforrások

Minden adat az alkalmazáson keresztül, felhasználói bevitellel kerül a rendszerbe (saját edzések és szettek) — nincs külső, valós személyes adat; a bemutatóhoz fiktív minta-adatok használhatók.

### Komponensek és adatút

```
Streamlit UI → FastAPI végpont → szolgáltatás/domain réteg → SQLAlchemy → adatbázis
                                                                  ↓
Streamlit UI ← FastAPI válasz (JSON) ← ugyanaz az adatút visszafelé
```

Példa: edzés rögzítése az Edzésnapló oldalon → `POST /sessions` → `create_workout_session_with_sets` → `SetEntry`/`WorkoutSession` mentése → válasz visszamegy a felületre, majd a Progresszió oldal a `GET /exercises/{id}/progression` végponton keresztül olvassa vissza és jeleníti meg diagramon.

### Paradigmák a kódban

| Paradigma | Hol | Mit csinál |
|---|---|---|
| Funkcionális | `backend/domain/progression.py`, `maintenance/decision.py`, `maintenance/snapshot.py::compare_snapshots` | Tiszta, mellékhatásmentes függvények: `estimate_one_rm`, `linear_trend`, `compute_progression`; a csere-döntés (`decide_swap`, `parse_maintenance_at`) és a visszaállítás tartalmi összevetése |
| Objektumorientált | `backend/services/progression_analyzer.py::ProgressionAnalyzer`, `maintenance/db_swap_controller.py::DBSwapController`, `maintenance/provider_client.py::RenderClient`, `maintenance/state.py::StateStore` | Valódi viselkedésű osztályok: adatbázis → domain leképezés; a csere lépései munkatársakkal (Render, backend, mentés); korlátozott újrapróbálkozású API-kliens; atomikus állapotmentés |
| Procedurális | `backend/services/workout_service.py::create_workout_session_with_sets`, `maintenance/workflow.py::run_maintenance_cycle`, `maintenance/backup.py` | Lineáris, függvényekre bontott munkafolyamatok (edzés mentése; ellenőrzés → döntés → lépések; pg_dump/pg_restore) |

## Használat

### Előfeltételek

- Python **3.12**
- Virtuális környezet: `python -m venv venv` majd aktiválás

### Telepítés

```bash
python -m venv venv
# Windows: venv\Scripts\activate | Unix: source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env   # majd töltsd ki a szükséges értékeket
```

### Adatbázis

Fejlesztéshez alapértelmezetten SQLite-ot használ (`DATABASE_URL=sqlite:///./dev.db` a `.env`-ben), a táblák a backend indulásakor automatikusan létrejönnek (meglévő táblát sosem dob el). Éles/felhős környezetben Render PostgreSQL 16 (psycopg2 driver), mert a 6. szakasz szerinti csere szolgáltatói API-val kezelhető példányt igényel (lásd [Karbantartás](#karbantartás)).

A karbantartó programhoz szükséges még: a PostgreSQL kliens-eszközök (`pg_dump`, `pg_restore`, legalább a szerver főverziója, azaz 16+) a `PATH`-on, és egy üres, helyi PostgreSQL adatbázis a próbavisszaállításhoz (`REHEARSAL_DATABASE_URL`).

### Tesztkörnyezet

A tesztek egy elkülönített, memóriabeli SQLite adatbázist hoznak létre (`tests/conftest.py` a backend importálása előtt memóriabeli `DATABASE_URL`-t állít be), így nem érintik a fejlesztői `dev.db`-t, és nem kell hozzájuk Postgres, hálózat vagy felhős fiók.

### Indítás

Külön terminálokból:

```bash
uvicorn backend.main:app --reload
streamlit run frontend/app.py
python -m maintenance.scheduler
python -m pytest
```

Vagy egyszerre mindhárom komponens (backend, frontend, karbantartó ütemező):

```bash
python start.py
```

Leállítás: `Ctrl+C` (a `start.py` minden gyermekfolyamatot tisztán leállít).

### API végpontok

| Metódus | Útvonal | Leírás |
|---|---|---|
| POST | `/exercises` | gyakorlat létrehozása |
| GET | `/exercises` | gyakorlatok listázása, `?category=` szűréssel |
| POST | `/sessions` | edzés rögzítése szettekkel |
| GET | `/sessions` | edzések listázása, `?start_date=&end_date=` szűréssel |
| GET | `/sessions/{id}` | edzés részletei |
| GET | `/exercises/{id}/progression` | progresszió számítása, `?lookback_weeks=` (1–52) |

Hibakezelés: érvénytelen bemenet `422`, ismeretlen gyakorlatra hivatkozó edzés `400`, már létező gyakorlatnév `409`, nem létező edzés/gyakorlat `404`, írászár (DB-csere) alatt minden írás `503`.

Belső, csak a karbantartó programnak szóló végpontok (`X-Maintenance-Token` fejléccel): `POST /internal/maintenance-mode` (írászár be/ki), `POST /internal/write-check` (kontrollált próba-írás). A `GET /health` infrastruktúra-végpont.

Interaktív dokumentáció futás közben: `http://localhost:8000/docs`.

### Felhős elérhetőség

- Frontend (Streamlit Community Cloud): _TODO: link a telepítés után_
- Backend (Render): _TODO: link a telepítés után_

### Ismert korlátok

- A Render ingyenes webszolgáltatása inaktivitás esetén elalhat, és fájlrendszere nem tartós — ezért is szükséges a PostgreSQL-alapú, programozott adatbázis-csere (lásd Karbantartás).
- Az ingyenes Render PostgreSQL-példány 30 nap után lejár, és workspace-enként egyszerre csak egy lehet — ezért a csere a régi példány törlésével jár (előtte helyi próbavisszaállítással).
- A progresszió-szabály csak 1–12 ismétléses szetteket vesz figyelembe (az Epley-formula e fölött megbízhatatlan); a felület ezt jelzi.
- Az edzésnapló append-only: rögzített edzést a felületen nem lehet szerkeszteni vagy törölni (lásd Tervezési döntések).

## Karbantartás

A `maintenance/` csomag egy önálló, aszinkron vezérlőprogram, amely a backendtől és a cserélt adatbázistól függetlenül, a helyi gépen fut. Ütemezetten ellenőrzi a Render Postgres példányt, és ha tervezett karbantartás vagy közelgő lejárat miatt indokolt, programozottan új példányra cseréli az adatbázist.

### Indítás és ütemezés

```bash
python -m maintenance.scheduler            # ütemezett ellenőrzés (CHECK_INTERVAL_SECONDS, alapból óránként)
python -m maintenance.scheduler --once     # egyetlen ellenőrzés/csere-ciklus, majd kilép
python -m maintenance.scheduler --resume   # egy leállt (halted) futás folytatásának jóváhagyása
```

Minden ciklus valódi aszinkron hívással (`httpx.AsyncClient`, `maintenance/workflow.py::check_instance_status`) lekéri az aktív példány állapotát és lejárati idejét (`expiresAt`), naplózza, majd a tiszta `decide_swap` függvény dönt. Cserét csak két dolog indít: a konfigurált tervezett karbantartási időpont (`MAINTENANCE_AT`) elérése, vagy ha a lejárat `EXPIRY_WARNING_DAYS` napon belül van. Puszta elérhetetlenség vagy hiányzó adat soha nem indít cserét, és egy már teljesített kérés (`fulfilled_trigger`) sem indít újat. Minden időpontot UTC-ben hasonlítunk össze; az időzóna nélkül megadott `MAINTENANCE_AT` a `TIMEZONE` szerint értendő.

Egyszerre csak egy ütemező futhat: egy OS-szintű fájlzár (`maintenance/state/scheduler.lock`) megakadályozza a második példány indulását, és a zárat összeomláskor is felszabadítja az operációs rendszer.

### Jogosultság és konfiguráció

- **Előzetes jogosítás:** `MAINTENANCE_SWAP_ENABLED=true` nélkül a program csak ellenőriz és naplóz, felhős erőforráshoz nem nyúl.
- **Kezelt erőforrások:** csak a `RENDER_WEB_SERVICE_ID` szolgáltatást, az aktív példányt (`ACTIVE_DB_INSTANCE_ID`, utána a program saját nyilvántartása) és a saját maga által létrehozott példányokat kezeli. Az előfeltétel-ellenőrzés megtagadja a cserét, ha az aktív példány nem a `RENDER_OWNER_ID` workspace-é.
- **Titkok:** `RENDER_API_KEY`, `MAINTENANCE_TOKEN`, a kapcsolati sztringek csak környezeti változóban vannak (lásd `.env.example`); se a naplóba, se a mentés metaadatába nem kerülnek.
- A backend belső végpontjai a `MAINTENANCE_TOKEN` nélkül `401`-et adnak.

### A csere lépései

A `DBSwapController` minden lépés után tartósan rögzíti az utoljára befejezett lépést (`maintenance/state/run_state.json`, atomikus írással):

1. **Előfeltételek** — konfiguráció, `pg_dump`/`pg_restore`, írható mentési hely, Render-jogosultság, az aktív példány tulajdonosa; az új példány paraméterei explicitek (`TARGET_DB_PLAN`/`REGION`/`VERSION`).
2. **Írások leállítása, majd mentés** — az írászár a futó backendben azonnal (`POST /internal/maintenance-mode`), és a Render `WRITES_FROZEN=true` környezeti változóban is bekapcsol, így egy újraindulás vagy a későbbi redeploy után is zárolva marad. `WRITE_DRAIN_SECONDS` várakozás a folyamatban lévő írásokra, utána tartalmi pillanatkép a backend olvasó végpontjairól, majd `pg_dump`.
3. **Próbavisszaállítás, majd a régi példány törlése** — mivel ingyenesen csak egy példány lehet, a régit törölni kell. Előtte a mentést ugyanazzal a `restore_database` művelettel visszaállítjuk a helyi `REHEARSAL_DATABASE_URL` adatbázisba, és a backend saját végpontjaival (memóriában futtatva) ellenőrizzük: a gyakorlatok, az edzések a szettjeikkel és gyakorlatonként a progresszió-szabály eredménye pontosan egyezik a mentéskorival, és egy új rekord ID-ja nem ütközik. Csak ezután jön a törlés.
4. **Új példány és visszaállítás** — a példány neve a futás azonosítójából képződik (`edzesnaplo-db-<run_id>`), így megszakadás után név szerint megtalálható. Időkorlátos várakozás az `available` állapotra, majd `pg_restore`.
5. **Átirányítás és ellenőrzés** — csak a backend `DATABASE_URL`-je változik, utána redeploy, és megvárjuk a `live` állapotot. Ezután a backend olvasó végpontjainak a mentéskori pillanatképpel azonos tartalmat kell adniuk (nem elég egy health check), és egy kontrollált próba-írás (`POST /internal/write-check`) igazolja az írhatóságot, miközben a normál írások zárolva vannak. Az új aktív példányt és a teljesített kérést még az írások feloldása előtt tartósan rögzítjük, és csak ezután oldjuk fel a zárat (`WRITES_FROZEN=false`).

### Hibák, leállás és folytatás megszakítás után

- **Újrapróbálkozás:** a hálózati hibákat, az 5xx és a 429 válaszokat korlátozott számban (4×, exponenciális várakozással) próbáljuk újra (`maintenance/retry.py`). Jogosultsági hibát (401/402/403) soha, és fizetős csomagra sem lép a program magától.
- **Bizonytalan kimenetelű létrehozás:** ha a példány létrehozása válasz nélkül vagy 5xx-szel bukik el, nem próbáljuk vakon újra, hanem név szerint megkeressük a példányt; ha nem található, a program leáll.
- **Folytatás megszakítás után:** a következő ciklus az utoljára befejezett lépés utáni lépéstől folytat, nem az elejéről. Előtte egyeztet (`reconcile`): a rögzített mentésnek épnek kell lennie (SHA-256), a rögzített célpéldánynak léteznie kell a Rendernél. Minden lépés megismételhető: a törlés elfogadja a 404-et, a létrehozás előbb név szerint keres, a visszaállítás `--clean` módban fut, így nem keletkezik duplikált példány vagy adat.
- **Biztonságos leállás:** bármilyen hiba „halted” állapotba viszi a futást. A mentés és a futásállapot megmarad, és az írászár bekapcsolva marad (az utolsó ismert jó állapot). Ezután a program semmit nem csinál, amíg egy ember a naplót és a Render felületét átnézve le nem futtatja a `--resume` parancsot. Ez a leállás előtti utolsó befejezett lépéstől folytatja.
- **Hiányzó mentés:** ha nincs ép mentés, a program leáll, és soha nem pótolja kitalált vagy mintaadattal.

### Mentés formátuma és helye

Egy mentés a `maintenance/backups/` mappában (gitignore-olva) két fájl:
- `<példány>_<UTC időbélyeg>.dump`: a `pg_dump --format=custom` kimenete, benne a séma, a rekordok, a kapcsolatok és a szekvenciák, azaz az ID-kiosztás állapota;
- azonos nevű `.json`: forrás-azonosító, létrehozási idő, SHA-256 és a mentéskori tartalmi pillanatkép.

A mentés független a forrás példánytól és a backendtől. A visszaállítás ugyanazon PostgreSQL főverzió-családon belül kompatibilis.

### A teljes csereprőba naplója és eredménye

_TODO: a valódi Render-fiók elleni csereprőba (`MAINTENANCE_AT` beállításával, `python -m maintenance.scheduler --once`) után ide kerül a `maintenance/logs/scheduler.log` titkoktól megtisztított kivonata és az eredmény összefoglalója (6.5. szakasz)._

## Ellenőrzések

### Tesztek futtatása

```bash
python -m pytest -v
```

41 teszteset (25 tesztfüggvény, paraméterezéssel):
- `tests/test_progression.py` (10): a progressziós szabály normál, határ- és érvénytelen esetei, kis, fix adatokkal.
- `tests/test_maintenance.py` (26): a csere-döntés (átmeneti hiba, lejárat, teljesített kérés, időzóna), a mentés épsége (hiányzó vagy sérült mentés), a valódi workflow és vezérlő hamis Render/backend ellen (helyes lépéssorrend, jogosítás nélkül nincs csere, folytatás megszakítás után duplikáció nélkül, leállás hiányzó célpéldánynál vagy eltérő tartalomnál), a próbavisszaállítás valódi backend-végpontokkal, és a korlátozott újrapróbálkozás szabályai.
- `tests/test_api_integration.py` (5): valódi kérés → feldolgozás → teszt-adatbázis → válasz a FastAPI `TestClient`-tel, státuszkód-, tartalom- és adatbázis-állapot-ellenőrzéssel.

A tesztek nem érnek el élő szolgáltatást, és nem módosítanak felhős erőforrást.

### CI

A `.github/workflows/ci.yml` minden `push`/`pull_request` eseményen lefuttatja ugyanezt a tesztcsomagot (Python 3.12, Ubuntu).

### Dokumentált hibajavítási eset

_TODO: a GitHub-repó újraindítása után ide kerülnek a hibás (piros) és a javított (zöld) CI-futás linkjei, valamint a hozzájuk tartozó commitok._

Tervezett eset (egy `main`-hez nem kötött, deployhoz nem kapcsolt branch-en):

- **Tünet/hiba**: `estimate_one_rm` ismétlésszám-felső-határ ellenőrzése hibásan `MAX_REPS + 1`-re lazul, így `reps=13` érvényesnek tűnik.
- **Ok**: off-by-one hiba a validációs feltételben.
- **Észlelés**: a meglévő `test_estimate_one_rm_invalid_reps[100.0-13]` paraméterezett teszteset elbukik, a CI pirosra vált.
- **Javítás**: a határ visszaáll `MAX_REPS`-re, a CI zöldre vált.
- **Ellenőrzés**: a branch csak a zöld futás után kerül `--no-ff` merge-dzsel a `main`-be.

## Források és bővítések

### AI-eszközök használata

Ez a projekt Claude (Anthropic; Claude Sonnet 5 és Claude Opus 5.5, a Claude Code CLI-ben) segítségével készült: tervezés (architektúra, adatmodell, API-design), kódgenerálás (backend, frontend, karbantartó program, tesztek) és hibakeresés (pl. a Streamlit `sys.path` importhiba felismerése és javítása; a teljes projekt átnézése, ami a karbantartó program több hibáját tárta fel, és a Render API-hívások ellenőrzése a hivatalos API-dokumentáció alapján). Minden generált kódot a hallgató átnézett, helyi futtatással és a teszt-csomaggal ellenőrzött.

### Adatok eredete

Minden adat saját bevitelű vagy fiktív minta-adat; külső adatforrás nem került felhasználásra.

### Bővítések

_Nem választottunk opcionális bővítést (9. szakasz)._

## Tervezési döntések

1. **Append-only edzésnapló szerkesztés és törlés nélkül.** Probléma: a felhasználó hibásan rögzíthet egy edzést, ezért kézenfekvő lenne a szerkesztés és a törlés. Mérlegelt alternatíva: teljes CRUD, verziózott módosítási előzménnyel. Választott megoldás: a rögzített edzések nem módosíthatók. Egy ugyanarra az adatra mindig ugyanazt adó progresszió-számítás így ellenőrizhető, és a DB-csere tartalmi ellenőrzése (mentéskori és visszaállított pillanatkép összevetése) is egyszerűbb; a feladat nem is kér teljes CRUD-ot. Korlát: egy hibás bejegyzés a felületről nem javítható, csak adatbázis-szinten.

2. **A Render PostgreSQL választása csereszolgáltatóként a Neon helyett.** Probléma: a 6. szakasz valódi, programozott felhős adatbázis-cserét igényel egy konkrét szolgáltató API-jával. Mérlegelt alternatíva: Neon (nincs automatikus lejárat, erős branch-alapú mentés/visszaállítás). Választott megoldás: Render Postgres, mert (a) a backend is Renderen fut, így egyetlen API-kulccsal kezelhető minden, és (b) az ingyenes példány valódi, nem szimulált 30 napos lejárata pontosan illeszkedik a specifikáció "közelgő lejárat" cseréletetőjéhez. Korlát: a Render workspace-enként csak egy ingyenes példányt enged, ezért a csere-folyamat előbb törli a régi példányt, mielőtt az újat létrehozná. Hogy ez ne járjon adatvesztéssel, a törlés előtt a mentést egy helyi Postgres adatbázisba próbaképp visszaállítjuk és tartalmilag ellenőrizzük (`DBSwapController.rehearse_restore`, majd `delete_old_instance`); cserébe a karbantartó géphez helyi Postgres kell, és a csere alatt néhány percig nincs élő adatbázis (ezalatt az írások zárolva vannak).

3. **Epley e1RM-formula + lineáris trend a progresszióhoz, más módszerek helyett.** Probléma: több 1RM-becslő formula létezik (Epley, Brzycki), és magas ismétlésszámnál egyik sem megbízható. Mérlegelt alternatíva: Brzycki-formula, vagy nyers térfogat (súly×ismétlés) követése formula nélkül. Választott megoldás: Epley-formula, de csak 1–12 ismétlés között megbízható tartományban, a heti meredekség alapú osztályozással (`improving`/`plateau`/`declining`). Korlát: feltételezi a pontos, önbevallott súly/ismétlés adatot, és nem veszi figyelembe a technikai kivitelezést vagy az RPE-t.
