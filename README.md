# GitHub Pulse

Dashboard locale per l'account GitHub `Daniele-Cangi`.

## Funzioni

- follower, seguiti e follow reciproci;
- account che non ricambiano il follow;
- follower che non sono ancora stati ricambiati;
- visualizzazioni e visitatori unici dei repository;
- cloni, cloner unici, referrer e contenuti popolari;
- storico giornaliero locale in SQLite.

GitHub non rende disponibili le visite al profilo personale né l'identità dei
visitatori. Le metriche di traffico riguardano esclusivamente i repository sui
quali l'account autenticato ha accesso in scrittura.

## Avvio

Fai doppio clic su `start.cmd`. In alternativa, apri PowerShell nella cartella
ed esegui:

```powershell
.\start.ps1
```

La dashboard si apre su <http://127.0.0.1:8765>. Per arrestarla, premi `Ctrl+C`
nella finestra PowerShell.

## Sicurezza e dati

L'applicazione ascolta soltanto su `127.0.0.1`. Non contiene e non memorizza token:
usa la sessione di GitHub CLI conservata nel keyring di Windows. Il database viene
creato in `data/github-pulse.sqlite3`.

GitHub fornisce il traffico degli ultimi 14 giorni. Apri il repository nella
dashboard almeno una volta ogni due settimane per conservare uno storico continuo.
