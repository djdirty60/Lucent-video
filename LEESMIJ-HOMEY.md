# Lucent Video en Homey: filmavond

*English version: [README-HOMEY.md](README-HOMEY.md)*

Met Lucent Video kan je Homey automatisch de woonkamer in filmstand laten zetten. Als je een film of aflevering start, gaan de lampen omlaag. Bij de aftiteling gaan ze langzaam weer omhoog, en na afloop staat alles weer zoals het was.

Dit document is voor wie de Flow in Homey maakt. Je hoeft niets van programmeren te weten.

---

## Hoe werkt het?

Lucent Video stuurt op vaste momenten een kort seintje naar Homey. Wat er dan gebeurt, bepaal je zelf in een Flow in Homey.

Lucent stuurt alleen seintjes bij video's van **10 minuten of langer**. Bij trailers gebeurt er dus niets.

---

## Stap 1: Lucent Video instellen

1. Open in Kodi de instellingen van **Lucent Video**.
2. Zoek de instelling voor **Homey**.
3. Vul je **Homey-ID** in. Die vind je in de Homey-app onder **Instellingen > Algemeen**.
   Heb je een Homey Pro (2023 of nieuwer) of een Homey Pro mini? Dan mag je ook het **IP-adres** invullen.
4. Druk op de knop **Homey testen**.
5. Zie je "Homey heeft het seintje ontvangen"? Dan werkt het.

---

## Stap 2: De seintjes die Lucent stuurt

Elk seintje gaat naar de webhook met het evenement **`lucent`**. Het seintje heeft een **tag**. Die tag zegt wat er gebeurt.

| Tag | Wanneer | Idee voor wat Homey doet |
| --- | --- | --- |
| `start-film` | Een film begint | Lampen laag, rolgordijnen dicht |
| `start-serie` | Een aflevering begint | Lampen iets minder laag |
| `pauze` | Je drukt op pauze | Eén lamp halfvol, zodat je iets kunt pakken |
| `verder` | Je kijkt verder na een pauze | Terug naar de film- of seriestand |
| `aftiteling` | De aftiteling van een film begint (zie hieronder) | Lampen langzaam feller |
| `einde` | De video stopt of is afgelopen | Alles terug zoals het was |
| `test` | Alleen bij de knop Homey testen | Niets, of een melding om te proberen |

Goed om te weten:

- **`aftiteling`** komt alleen bij **films**, niet bij afleveringen.
- **`aftiteling`** komt alleen als TheIntroDB weet waar de aftiteling van die film begint. Weet TheIntroDB dat niet, dan komt alleen `einde`.
- Heeft een film nog een scène **tijdens of na de aftiteling**, dan stuurt Lucent **geen** `aftiteling`. De lampen blijven dan gedimd tot `einde`, zodat je die scène in het donker ziet.
- Tussen twee afleveringen die vanzelf achter elkaar spelen, stuurt Lucent **geen** `einde`, maar wel een nieuwe `start-serie`.
- Oude versies van Lucent Video (tot en met 0.35.6) sturen `start` in plaats van `start-film` of `start-serie`. Laat je Flow `start` als film behandelen, dan werkt hij met beide.

---

## Stap 3: De Flow maken in Homey

Gebruik een **Advanced Flow**. Daarin kan één webhook-kaart naar meerdere takken leiden.

1. Open de Homey-app en maak een nieuwe **Advanced Flow**.
2. Kies als begin (Als...) de kaart **Logica > Een webhook is ontvangen**.
3. Vul bij **Evenement** in: `lucent`
   Let op: precies zo, met kleine letters en **zonder spatie** erachter.
4. Zet achter de webhook-kaart een kaart **Logica > is precies**.
   - Sleep de tag **Tag** van de webhook-kaart in het eerste vak.
   - Vul in het tweede vak de tag in, bijvoorbeeld `start-film`.
5. Zet achter de "is precies"-kaart wat er moet gebeuren, bijvoorbeeld een lamp dimmen.
6. Herhaal stap 4 en 5 voor elke tag die je wilt gebruiken: `start-serie`, `pauze`, `verder`, `aftiteling` en `einde`.
7. Zet de Flow **aan** en sla hem op.

---

## Voorbeeld: Filmavond (Lucent)

Zo werkt de Flow **Filmavond (Lucent)**. Je kunt hem als voorbeeld gebruiken.

### Wat Homey onthoudt (variabelen)

De Flow gebruikt een paar variabelen in **Logica**:

| Variabele | Soort | Waarvoor |
| --- | --- | --- |
| Filmavond actief | ja/nee | Staat de filmstand al aan? |
| Filmavond soort | tekst | `film`, `serie` of `einde` |
| Filmavond grote lamp was aan | ja/nee | Om de grote lamp straks goed terug te zetten |
| Filmavond eettafel was aan | ja/nee | Idem voor de eettafellamp |
| Filmavond tafellamp was aan | ja/nee | Idem voor de tafellamp |
| Filmavond tafellamp helderheid | getal | De oude helderheid van de tafellamp |

### Bij `start-film` of `start-serie`

1. Zet **Filmavond soort** op `film` of `serie`.
2. Zet de Flow **Tafellamp** (die op de lichtsensor reageert) **uit**. Anders gaat de lamp vanzelf weer feller.
3. Staat **Filmavond actief** nog op **nee**? Dan is dit de eerste start van de avond:
   - onthoud of de grote lamp, de eettafellamp en de tafellamp aan stonden, en hoe fel de tafellamp was;
   - zet de grote lamp en de eettafellamp uit;
   - zet **Filmavond actief** op **ja**.
4. Film: tafellamp op **8%**, tv-kast verlichting aan, rolgordijnen dicht.
   Serie: tafellamp op **25%**, tv-kast verlichting aan.

### Bij `pauze`

- Tafellamp op **50%**, zodat je iets kunt pakken.

### Bij `verder`

- Tafellamp terug naar **8%** (film) of **25%** (serie). Kijk daarvoor naar **Filmavond soort**.

### Bij `aftiteling` (nieuw)

Hier mogen de lampen alvast **langzaam** feller worden, net als in de bioscoop.

1. Zet de tafellamp met de kaart **Dimmen** op bijvoorbeeld **40%**, met een **overgangsduur van 20 seconden**. Zo wordt het rustig lichter.
2. Doe nog **niet** alles terug. Dat gebeurt pas bij `einde`. Zo kan je de aftiteling nog uitkijken.

### Bij `einde`

1. Zet **Filmavond soort** op `einde`.
2. Wacht **30 seconden**.
3. Kijk of **Filmavond soort** nog steeds `einde` is.
   - Is het iets anders, dan is er intussen een nieuwe aflevering begonnen. Doe dan niets, de filmstand blijft staan.
   - Is het nog `einde`, ga dan verder.
4. Zet alles terug zoals het was:
   - lampen die aan stonden, gaan weer aan;
   - lampen die uit stonden, blijven uit;
   - de tafellamp krijgt zijn oude helderheid terug.
5. Zet **Filmavond actief** op **nee**.
6. Wacht nog **10 seconden** en zet de Flow **Tafellamp** (lichtsensor) weer **aan**.

---

## Werkt het niet?

| Wat je ziet | Wat je doet |
| --- | --- |
| Er gebeurt helemaal niets | Druk op **Homey testen** in Lucent Video. Controleer je Homey-ID of IP-adres. |
| Homey testen zegt "code 404" | Het Homey-ID klopt niet. Lokaal via IP werkt alleen op een Homey Pro (2023 of nieuwer) of Homey Pro mini. |
| Homey testen lukt, maar de Flow start niet | Kijk of het evenement precies `lucent` is, met kleine letters en **zonder spatie** erachter. |
| De Flow start, maar er gebeurt verder niets | Kijk of de tag in de kaart **is precies** goed geschreven is, met kleine letters en een streepje: `start-film`, niet `start film`. |
| Bij de aftiteling gebeurt niets | Normaal bij films die TheIntroDB niet kent, en bij films met een scène na de aftiteling. Dan komt alleen `einde`. |
| Na een crash gaan de lampen niet goed terug | Lucent kon `einde` niet sturen. Zet je lampen even met de hand goed en zet **Filmavond actief** op **nee**. |

Kijk in de Homey-app bij de Flow onder **Geschiedenis** of de Flow gestart is. Zo zie je of het seintje is aangekomen.
