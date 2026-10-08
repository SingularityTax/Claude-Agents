# Phase 0 – Einrichtung Microsoft 365

Diese Anleitung ist zum Abarbeiten gedacht. Jeder Schritt endet mit einer **Prüfung**. Bitte schicken Sie mir nach jedem Block die Ausgabe der Prüfung. Dann prüfe ich, bevor es weitergeht.

Benötigt: Global Admin bzw. Exchange Admin, PowerShell 7 und das Modul `ExchangeOnlineManagement`.

```powershell
Install-Module ExchangeOnlineManagement -Scope CurrentUser   # einmalig
Connect-ExchangeOnline -UserPrincipalName marko.kaiser@singularity.tax
```

---

## Block A – Bestandsaufnahme (nur lesen, nichts wird geändert)

```powershell
# 1. Sind die Adressen eigene Postfächer oder Aliase?
"filing","gethelp","hello","welcome","compliance","helphub","onboarding" | ForEach-Object {
  Get-Recipient "$_@singularity.tax" | Select-Object @{n='Adresse';e={"$_@singularity.tax"}}, RecipientTypeDetails, PrimarySmtpAddress
}

# 2. Wer hat Vollzugriff bzw. SendAs auf die Sammelpostfächer? Das sind die Mitarbeiter, die überwacht werden.
"filing","gethelp","welcome","compliance","helphub","onboarding" | ForEach-Object {
  Get-MailboxPermission "$_@singularity.tax" | Where-Object { $_.User -like '*@*' } | Select-Object Identity, User, AccessRights
  Get-RecipientPermission "$_@singularity.tax" | Where-Object { $_.Trustee -like '*@*' } | Select-Object Identity, Trustee, AccessRights
}

# 3. Werden Gesendete Elemente im Sammelpostfach abgelegt, wenn ein Mitarbeiter "als" filing@ sendet?
"filing","gethelp","welcome","compliance","helphub","onboarding" | ForEach-Object {
  Get-Mailbox "$_@singularity.tax" | Select-Object PrimarySmtpAddress, MessageCopyForSentAsEnabled, MessageCopyForSendOnBehalfEnabled, AuditEnabled
}

# 4. Ist das Unified Audit Log aktiv?
Get-AdminAuditLogConfig | Select-Object UnifiedAuditLogIngestionEnabled
```

**Prüfung:** Schicken Sie mir die komplette Ausgabe. Daran sehen wir, ob hello@ ein Alias ist, wer zum Mitarbeiterkreis gehört und was in Block B umgestellt werden muss.

---

## Block B – Protokollierung einschalten

Damit erkennbar ist, **wer** als filing@ geantwortet hat:

```powershell
# Unified Audit Log (falls in A.4 = False)
Set-AdminAuditLogConfig -UnifiedAuditLogIngestionEnabled $true

# Kopie in "Gesendete Elemente" des Sammelpostfachs + Audit an
"filing","gethelp","welcome","compliance","helphub","onboarding" | ForEach-Object {
  Set-Mailbox "$_@singularity.tax" -MessageCopyForSentAsEnabled $true -MessageCopyForSendOnBehalfEnabled $true -AuditEnabled $true
  Set-Mailbox "$_@singularity.tax" -AuditDelegate @{Add="SendAs","SendOnBehalf"}
}
```

**Prüfung:** Block A, Schritt 3 erneut ausführen. Alle Werte müssen `True` sein.

---

## Block C – Postfach für Benachrichtigungen

Eskalationsmails sollen nicht aus einem persönlichen Postfach kommen:

```powershell
New-Mailbox -Shared -Name "SLA Monitor" -PrimarySmtpAddress sla-monitor@singularity.tax
```

---

## Block D – App-Registrierung in Entra

Im Entra Admin Center (entra.microsoft.com):

1. **Anwendungen → App-Registrierungen → Neue Registrierung**
   - Name: `Singularity Inbox SLA Monitor`
   - Kontotypen: *Nur Konten in diesem Organisationsverzeichnis*
   - Umleitungs-URI: leer lassen
2. Notieren Sie **Anwendungs-ID (Client-ID)** und **Verzeichnis-ID (Mandanten-ID)**. Beides ist nicht geheim.
3. **API-Berechtigungen → Berechtigung hinzufügen**, jeweils Typ **Anwendungsberechtigungen**:
   - *Office 365 Management APIs* → `ActivityFeed.Read` (Audit-Log: wer hat als filing@ gesendet)
   - *Microsoft Graph* → `User.Read.All` (Mitarbeiterliste, Abwesenheiten)
   - **Kein** `Mail.Read` und **kein** `Mail.Send` hier eintragen. Die Rechte auf die Postfächer kommen in Block E, beschränkt auf genau die betroffenen Postfächer. Eine Freigabe hier würde Zugriff auf **alle** Postfächer der Firma geben.
4. **Administratorzustimmung erteilen** klicken.
5. Zugangsdaten: **Zertifikate & Geheimnisse → Geheimen Clientschlüssel hinzufügen**, Laufzeit 12 Monate.
   - Den Wert **nicht** in den Chat kopieren. Er kommt später in den Secret-Speicher der Laufzeitumgebung (Azure Key Vault bzw. Umgebungsvariable).

Zusätzlich in **Unternehmensanwendungen → Singularity Inbox SLA Monitor** die **Objekt-ID** notieren. Das ist die ID der Unternehmensanwendung, nicht die der App-Registrierung.

**Prüfung:** Schicken Sie mir Client-ID, Mandanten-ID und Objekt-ID der Unternehmensanwendung.

---

## Block E – Postfachzugriff begrenzen (RBAC for Applications)

### Entscheidung: Mitarbeiterpostfächer werden einbezogen (Option A, entschieden 08.10.2026)

Mitarbeiter antworten teils aus dem eigenen Postfach, ohne die Sammeladresse in Kopie zu setzen. Diese Antworten sieht der Monitor **nur**, wenn er die Gesendeten Elemente der Mitarbeiter lesen darf. Exchange kann das Leserecht nicht auf einen einzelnen Ordner beschränken. Technisch hat die App dann Lesezugriff auf das ganze Postfach. Der Code liest ausschließlich `SentItems` und nur Mails an externe Empfänger.

| Option | Folge |
|--------|-------|
| **A (gewählt):** Mitarbeiterpostfächer einbeziehen | vollständige Erfassung. In der Mitarbeiterinformation (DSGVO) muss stehen, dass der Monitor gesendete Mails an Mandanten auswertet |
| B: nur Sammelpostfächer | Antworten aus persönlichen Postfächern ohne CC gelten als **nicht beantwortet** und lösen Eskalationen aus. Funktioniert nur mit der harten Regel „Antworten immer aus dem Sammelpostfach oder mit CC“ |

Fiona und Marko werden in **beiden** Optionen nicht überwacht, sie sind die Empfänger.

```powershell
$AppId    = "<Client-ID aus Block D>"
$ObjectId = "<Objekt-ID der Unternehmensanwendung aus Block D>"

# 1. App in Exchange bekannt machen
New-ServicePrincipal -AppId $AppId -ObjectId $ObjectId -DisplayName "Singularity Inbox SLA Monitor"

# 2. Postfächer markieren, die der Monitor lesen darf
"filing","gethelp","welcome","compliance","helphub","onboarding" | ForEach-Object {
  Set-Mailbox "$_@singularity.tax" -CustomAttribute10 "SLAMonitor"
}
# Mitarbeiter (Option A): Liste aus Block A.2. Fiona und Marko bleiben bewusst draußen.
$Mitarbeiter = @(
  # "vorname.nachname@singularity.tax",
)
$Mitarbeiter | ForEach-Object { Set-Mailbox $_ -CustomAttribute10 "SLAMonitor" }

Set-Mailbox "sla-monitor@singularity.tax" -CustomAttribute11 "SLAMonitorSend"

# 3. Bereiche und Rollen
New-ManagementScope -Name "SLA Monitor – Lesen"  -RecipientRestrictionFilter "CustomAttribute10 -eq 'SLAMonitor'"
New-ManagementScope -Name "SLA Monitor – Senden" -RecipientRestrictionFilter "CustomAttribute11 -eq 'SLAMonitorSend'"

New-ManagementRoleAssignment -App $AppId -Role "Application Mail.Read"            -CustomResourceScope "SLA Monitor – Lesen"
New-ManagementRoleAssignment -App $AppId -Role "Application MailboxSettings.Read" -CustomResourceScope "SLA Monitor – Lesen"
New-ManagementRoleAssignment -App $AppId -Role "Application Mail.Send"            -CustomResourceScope "SLA Monitor – Senden"
```

**Prüfung:**

```powershell
Test-ServicePrincipalAuthorization -Identity $AppId -Resource filing@singularity.tax      # erwartet: Mail.Read = True
Test-ServicePrincipalAuthorization -Identity $AppId -Resource marko.kaiser@singularity.tax # erwartet: kein Zugriff (InScope = False)
# zusätzlich für einen Mitarbeiter aus $Mitarbeiter: erwartet Mail.Read = True
```

Bitte beide Ausgaben schicken. Die zweite ist die wichtigere, denn sie belegt, dass die App **nicht** überall lesen kann. Rechteänderungen brauchen bis zu 2 Stunden, bis sie greifen.

---

## Block F – Bitrix24 (Enterprise)

Im Portal: **Entwicklerressourcen → Andere → Eingehender Webhook**

- Berechtigungen: `sonet_group`, `im`, `task`, `crm`, `user`, `log`
- Benutzer: einen technischen Admin-Benutzer anlegen (z. B. „SLA Monitor“), damit der Webhook nicht an einer Person hängt. Er muss Mitglied aller Projekte sein bzw. Leserechte darauf haben.
- Die Webhook-URL enthält das Geheimnis. Sie gehört **nicht** in den Chat, sondern in den Secret-Speicher.

**Prüfung:** Rufen Sie `<webhook-url>/sonet_group.get.json` im Browser auf und schicken Sie mir nur die **Anzahl** der Projekte (`total`), nicht die Antwort selbst.

---

## Danach

Wenn A bis F erledigt sind, baue ich Phase 1 (Schattenbetrieb). Zuerst kommt der Rückimport der letzten 30 Tage, als Baseline pro Postfach und Mitarbeiter.

---

## Stand 08.10.2026 (Abend)

| Block | Status |
|-------|--------|
| A – Bestandsaufnahme | erledigt |
| B – Protokollierung | **erledigt, keine Änderung nötig.** Unified Audit Log aktiv. Kopie gesendeter Mails, Audit, `SendAs`/`SendOnBehalf` und `MailItemsAccessed` sind bei allen Sammelpostfächern bereits an |
| C – Postfach sla-monitor@ | erledigt (08.10.2026) |
| D – App-Registrierung | erledigt. `User.Read.All` und `ActivityFeed.Read` als Anwendungsberechtigung erteilt. Aufräumen optional: alte delegierte `User.Read.All`-Zustimmung widerrufen |
| E – Rechte begrenzen | erledigt und geprüft (Scope greift, Marko-Postfach gesperrt). Offen: ein Mitarbeiterpostfach nicht gefunden |
| F – Bitrix-Webhook | offen |

Erkenntnisse aus Block A:

- **hello@** ist ein eigenes Sammelpostfach mit Weiterleitung an welcome@ (Kopie bleibt in hello@). Der Monitor liest beide und dedupliziert über `internetMessageId`.
- **onboarding@** empfängt auch info@, anfragen@ und transfers@.
- **compliance@** ist ein Benutzerkonto ohne Stellvertreter, wird also vermutlich mit geteiltem Login genutzt. Antworten von dort sind keiner Person zuordenbar. Empfehlung: in ein Sammelpostfach umwandeln. Entscheidung offen.
- **Transportregel „Auto CC Get Help“:** Mails an `tax.notice@` gehen in Blindkopie an gethelp@. gethelp@ bleibt ein echter Posteingang, enthält aber zusätzlich alle Mails an tax.notice@. Der Monitor erkennt diese Kopien (Empfänger tax.notice@, gethelp@ nicht in To/Cc) und führt sie als Kategorie **„Steuerbescheid/Behördenpost über tax.notice@“**. Schickt ein Mandant ein Schreiben ein, läuft das SLA mit P1-Prüfung. Automatische Zustellungen ohne Mandantenbezug laufen ohne SLA.
- **Vier Mitarbeiter** können per Transportregel nicht aus dem persönlichen Postfach nach extern senden, sondern nur über die Sammelpostfächer. Sie werden überwacht, ihre persönlichen Postfächer kommen aber **nicht** in den Lesebereich (Block E). Ihre Antworten lassen sich nur über das SendAs-Audit zuordnen, deshalb ist Block B für sie entscheidend.
- Nicht überwacht werden Marko, Fiona, zwei Familienkonten und die technischen Konten admin.de@ und UK@.
