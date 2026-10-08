# Phase 1 – Installation und erster Lauf auf dem Mac mini

Ziel: die letzten 30 Tage aller Sammelpostfächer und der Bitrix-Mandantenprojekte einlesen und einen Baseline-Bericht erzeugen.
**Keine Eskalationen, keine Mails an Mitarbeiter.** Der Monitor liest nur. Gesendet wird in Phase 1 nichts.

Die Schritte 1, 2 und 4 bis 7 kann die Claude-Sitzung auf dem Mac ausführen. **Schritt 3 (Geheimnisse) macht Marko selbst im Terminal**, damit kein Geheimnis in einem Chat- oder Sitzungsprotokoll landet.

## 1. Code holen und installieren

```bash
cd ~
git clone https://github.com/markokaiser-hue/Claude-Agents.git sla-monitor   # oder: git -C ~/sla-monitor pull
cd ~/sla-monitor
git checkout claude/dreamy-cerf-5ze6wj
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q          # muss "passed" melden
```

## 2. Konfiguration anlegen

```bash
cp config.example.yaml config.yaml
```

In `config.yaml` den Block `staff:` mit den 15 überwachten Personen füllen. Die Liste steht in der Übergabe-Nachricht, nicht im Repo.

- `read_sent_items: true` gilt für die 11 Personen, deren persönliche Postfächer im App-Scope liegen.
- `read_sent_items: false` gilt für die 4 Personen, die nur über Sammelpostfächer senden können.
- Bei `signature_names` stehen die Namen, mit denen die Person unterschreibt (z. B. „Monica G J“). Sie werden nur gebraucht, wenn das Audit-Log keinen Treffer liefert.

`config.yaml` liegt in `.gitignore` und wird nie committet.

## 3. Geheimnisse in den Schlüsselbund (Marko, im eigenen Terminal)

```bash
cd ~/sla-monitor
.venv/bin/python -m keyring set sla-monitor graph-client-secret   # Wert aus dem Passwortmanager einfügen, Enter
.venv/bin/python -m keyring set sla-monitor bitrix-webhook-url    # komplette Webhook-URL, Enter
```

Die Eingabe bleibt unsichtbar. Gespeichert wird im macOS-Schlüsselbund unter „sla-monitor“.

## 4. Zugriffe prüfen (gibt keine Inhalte aus)

```bash
.venv/bin/sla-monitor check
.venv/bin/sla-monitor probe-graph
.venv/bin/sla-monitor probe-bitrix --groups 3
```

Erwartet wird:

- **`check`:** Bei allen 7 Sammelpostfächern und den 11 Mitarbeiterpostfächern steht „Zugriff OK“. Bitrix meldet 400 Projekte.
- **`probe-graph`:** Bei jedem Postfach stehen `internetMessageId`, `conversationId`, `uniqueBody` und `internetMessageHeaders` auf `ja`.
- **`probe-bitrix`:** Die Anzahl Projekte mit externen Mitgliedern ist plausibel. Für 3 Projekte gibt es Zahlen zu Chat und Feed.

Weicht etwas ab, nicht weitermachen, sondern die Ausgabe an Claude (Cloud-Sitzung) geben.

## 5. Audit-Log exportieren (wer hat „als“ Sammelpostfach gesendet)

In `pwsh` nach `Connect-ExchangeOnline -UserPrincipalName marko.kaiser@singularity.tax`:

```powershell
~/sla-monitor/scripts/export_sendas_audit.ps1 -Days 30
```

Danach im normalen Terminal:

```bash
.venv/bin/sla-monitor import-audit ~/sla-monitor-data/audit_sendas.csv
```

## 6. Rückimport und Bericht

```bash
.venv/bin/sla-monitor -v run --days 30
```

Die Laufzeit hängt vom Volumen ab. Bei rund 6.000 Mails und 400 Projekten sind 15 bis 45 Minuten realistisch, weil Bitrix gedrosselt abgefragt wird. Am Ende stehen zwei Pfade da:

- `~/sla-monitor-data/reports/baseline-<datum>.html`: der Bericht zum Öffnen im Browser
- `~/sla-monitor-data/reports/faelle-<datum>.csv`: alle Fälle für Excel

**Diese Dateien enthalten Mandanten- und Mitarbeiterdaten.** Sie bleiben auf dem Mac und kommen nicht ins Repo. Weitergegeben werden sie nur an Marko und Fiona.

## 7. Stichprobe (wichtig)

Vor jeder Bewertung von Mitarbeitern prüft Marko 20 zufällige Fälle aus der CSV gegen Outlook:

- Stimmt „offen/beantwortet“?
- Stimmt die antwortende Person?

Die Fehlerquote geht an die Cloud-Sitzung. Erst ab ≥ 95 % Treffern sind die Zahlen belastbar. Darunter werden die Regeln nachgeschärft.

## Bekannte Grenzen von Phase 1

- **Einstufung nur nach Regeln.** Die KI-Einstufung (`llm.enabled`) bleibt aus, bis die § 203-/AV-Regelung mit Anthropic dokumentiert ist. Folge: Einige Nachrichten werden fälschlich als Anfrage gezählt, z. B. ein Mandant, der Unterlagen nur schickt und nichts fragt.
- **Bitrix nur Projekt-Chat und Projekt-Feed.** Aufgaben-Kommentare und die CRM-Timeline (Account-Details) kommen in Phase 4.
- **„Gelesen von“ (`MailItemsAccessed`) ist noch nicht ausgewertet.**
- **Antworten per Telefon oder Teams sieht der Monitor nicht.**
