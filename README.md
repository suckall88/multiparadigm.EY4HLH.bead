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
| Funkcionális | `backend/domain/progression.py` | Tiszta, mellékhatásmentes függvények: `estimate_one_rm`, `linear_trend`, `compute_progression` |
| Objektumorientált | `backend/services/progression_analyzer.py::ProgressionAnalyzer`, `maintenance/db_swap_controller.py::DBSwapController` | Állapottal és valódi viselkedéssel rendelkező osztályok (DB-lekérdezés, cache, a csere-folyamat lépései) |
| Procedurális | `backend/services/workout_service.py::create_workout_session_with_sets`, `maintenance/workflow.py::run_maintenance_cycle` | Lineáris, függvényekre bontott munkafolyamatok |

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

Fejlesztéshez alapértelmezetten SQLite-ot használ (`DATABASE_URL=sqlite:///./dev.db` a `.env`-ben), a táblák a backend indulásakor automatikusan létrejönnek. Éles/felhős környezetben PostgreSQL szükséges (lásd [Karbantartás](#karbantartás)).

### Tesztkörnyezet

A tesztek egy elkülönített, memóriabeli SQLite adatbázist hoznak létre (`tests/conftest.py`), nem érintik a fejlesztői `dev.db`-t.

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
| GET | `/exercises/{id}/progression` | progresszió számítása, `?lookback_weeks=` |

Interaktív dokumentáció futás közben: `http://localhost:8000/docs`.

### Felhős elérhetőség

- Frontend (Streamlit Community Cloud): _TODO: link a telepítés után_
- Backend (Render): _TODO: link a telepítés után_

### Ismert korlátok

- A Render ingyenes webszolgáltatása inaktivitás esetén elalhat, és fájlrendszere nem tartós — ezért is szükséges a PostgreSQL-alapú, programozott adatbázis-csere (lásd Karbantartás).
- Az ingyenes Render PostgreSQL-példány 30 nap után lejár.

## Karbantartás

### Indítás és ütemezés

A `maintenance/scheduler.py` egy önálló, aszinkron Python-program (`python -m maintenance.scheduler`), amely `CHECK_INTERVAL_SECONDS` időközönként (alapértelmezetten óránként) valódi, `httpx.AsyncClient`-tel végzett API-hívással ellenőrzi az aktív adatbázis-példány állapotát és lejárati adatát a Render API-n keresztül (`maintenance/workflow.py::check_instance_status`).

### Jogosultság és konfiguráció

A csere végrehajtásához `RENDER_API_KEY`, `RENDER_OWNER_ID`, `RENDER_WEB_SERVICE_ID` szükséges (lásd `.env.example`), valamint a backend `MAINTENANCE_TOKEN`-je, amivel a karbantartó program hitelesíti magát a backend belső (`/internal/...`) végpontjainál.

### Karbantartási mód és folytatás megszakítás után

Csere előtt a vezérlő zárolja az írásokat (`POST /internal/maintenance-mode`), így minden normál `POST` végpont `503`-at ad vissza a csere alatt (lásd `backend/maintenance_mode.py`). Ha a program megszakad egy csere közben, újraindításkor a `maintenance/state.py::StateStore` perzisztált állapota alapján a `DBSwapController.reconcile()` ellenőrzi a szolgáltatónál, hogy a célpéldány már létrejött-e, mielőtt bármit tenne — így nem hoz létre duplikált erőforrást (lásd `tests/test_maintenance.py::test_resume_interrupted_run_does_not_create_duplicate_instance`).

### Mentés formátuma és helye

A mentés `pg_dump --format=custom` kimenete, mellékelt JSON metaadattal (forrás azonosító, UTC időbélyeg, SHA-256), a `maintenance/backups/` mappában (gitignore-olva, csak fiktív adatot tartalmaz, ha publikálásra kerül).

### A teljes csereprőba naplója és eredménye

_TODO: a valódi Render-fiók elleni csereprőba után ide kerül a napló-kivonat és az eredmény linkje/összefoglalója (lásd 6.5. szakasz)._

## Ellenőrzések

### Tesztek futtatása

```bash
python -m pytest -v
```

21 teszteset: 10 a progressziós szabályra (`tests/test_progression.py`, paraméterezett esetekkel), 7 a karbantartási döntésekre (`tests/test_maintenance.py`), 4 API-integrációs teszt a FastAPI `TestClient`-tel egy elkülönített teszt-adatbázis ellen (`tests/test_api_integration.py`).

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

Ez a projekt Claude (Anthropic, Claude Sonnet 5, Claude Code CLI) segítségével készült: tervezés (architektúra, adatmodell, API-design), kódgenerálás (backend, frontend, karbantartó program, tesztek) és hibakeresés (pl. a Streamlit `sys.path` importhiba felismerése és javítása). Minden generált kódot a hallgató átnézett, helyi futtatással és a teszt-csomaggal ellenőrzött.

### Adatok eredete

Minden adat saját bevitelű vagy fiktív minta-adat; külső adatforrás nem került felhasználásra.

### Bővítések

_Nem választottunk opcionális bővítést (9. szakasz)._

## Tervezési döntések

1. **Duplikált/módosított edzésbejegyzések kezelése.** Probléma: a felhasználó tévedésből duplán vagy hibásan rögzíthet egy edzést. Mérlegelt alternatíva: verziózott szerkesztési előzmény minden bejegyzéshez. Választott megoldás: az edzések append-only jellegűek, de kaszkádosan törölhetők és újra rögzíthetők (`created_at`/`updated_at` megőrzésével), ami egyszerűbb, mint egy teljes history-modell. Korlát: nincs diff-nézet a módosítások között.

2. **A Render PostgreSQL választása csereszolgáltatóként a Neon helyett.** Probléma: a 6. szakasz valódi, programozott felhős adatbázis-cserét igényel egy konkrét szolgáltató API-jával. Mérlegelt alternatíva: Neon (nincs automatikus lejárat, erős branch-alapú mentés/visszaállítás). Választott megoldás: Render Postgres, mert (a) a backend is Renderen fut, így egyetlen API-kulccsal kezelhető minden, és (b) az ingyenes példány valódi, nem szimulált 30 napos lejárata pontosan illeszkedik a specifikáció "közelgő lejárat" cseréletetőjéhez. Korlát: a Render workspace-enként csak egy ingyenes példányt enged, ezért a csere-folyamat előbb törli a régi példányt, mielőtt az újat létrehozná (lásd `DBSwapController.provision_target`).

3. **Epley e1RM-formula + lineáris trend a progresszióhoz, más módszerek helyett.** Probléma: több 1RM-becslő formula létezik (Epley, Brzycki), és magas ismétlésszámnál egyik sem megbízható. Mérlegelt alternatíva: Brzycki-formula, vagy nyers térfogat (súly×ismétlés) követése formula nélkül. Választott megoldás: Epley-formula, de csak 1–12 ismétlés között megbízható tartományban, a heti meredekség alapú osztályozással (`improving`/`plateau`/`declining`). Korlát: feltételezi a pontos, önbevallott súly/ismétlés adatot, és nem veszi figyelembe a technikai kivitelezést vagy az RPE-t.
