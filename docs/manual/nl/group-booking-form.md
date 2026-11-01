# Boekingsformulier van een team

Het formulier vraagt een inwoner altijd om voornaam, achternaam en e-mailadres. Of het ook een
telefoonnummer vraagt, stelt u in bij de [instellingen van het team](group-settings.md). Heeft
het team meer nodig, bijvoorbeeld een klantnummer of het onderwerp van het gesprek? Dan voegt u
hier eigen velden toe. U opent het scherm via **team → Instellingen → Boekingsformulier → Vragen beheren**. Een organisatiebeheerder of superuser kan het wijzigen.

Vraag alleen wat het team echt nodig heeft voor het gesprek. Elk extra veld is een gegeven dat
we moeten beschermen.

## Wat u nooit vraagt

Het formulier weigert vragen naar een burgerservicenummer, een paspoort of ander
identiteitsbewijs, en naar bijzondere persoonsgegevens zoals gezondheid, geloof, afkomst,
seksuele geaardheid, politieke voorkeur, lidmaatschap van een vakbond of strafrechtelijke
gegevens. Die controle is een vangnet, geen garantie: kies ook zelf geen vraag die daarop
neerkomt.

<!-- field: key -->
## Sleutel

De sleutel is de vaste naam van het veld, bijvoorbeeld `klantnummer`. Hij bestaat uit kleine
letters, cijfers en `_`, begint met een letter en is 2 tot 40 tekens lang. Na opslaan ligt hij
vast, en een sleutel die het team eerder gebruikte kan niet opnieuw. Zo betekent een antwoord
bij een afspraak altijd wat de vraag toen betekende.

<!-- field: type -->
## Soort

De soort bepaalt hoe het antwoord gecontroleerd wordt. U kunt geen eigen controle schrijven.

- **Korte tekst**: tot 100 tekens.
- **Vrij tekstveld**: tot 1000 tekens. Dit is altijd een persoonsgegeven, omdat een inwoner er
  alles in kan schrijven.
- **E-mailadres** en **telefoonnummer**: gecontroleerd zoals de vaste velden; altijd een
  persoonsgegeven. Een telefoonnummer zonder landcode lezen we als een nummer uit het land dat
  de organisatie heeft ingesteld (meestal Nederland), en we bewaren het met landcode.
- **Geheel getal**: met een onder- en bovengrens als u die opgeeft. Een nummer met voorloopnullen,
  zoals een klantnummer, is een korte tekst.
- **Datum**: met grenzen in dagen vanaf de dag van boeken.
- **Postcode (Nederland)**: vier cijfers en twee letters, bewaard als `1234 AB`.
- **Keuzelijst**: de inwoner kiest uit uw keuzes; er wordt nooit vrije tekst bewaard.
- **Vinkje**: aangevinkt of niet. Is het vinkje verplicht, dan moet het aangevinkt zijn,
  bijvoorbeeld voor toestemming.

Ook de soort ligt vast na opslaan. Wilt u een andere soort, zet het veld dan uit en voeg een
nieuw veld toe.

<!-- field: label_nl -->
## Vraag en toelichting

Elke vraag heeft een tekst in het Nederlands en in het Engels. Een toelichting is optioneel;
die staat onder de vraag. De inwoner ziet de taal van de pagina, en anders het Nederlands.

<!-- field: classification -->
## Persoonsgegeven

Elk nieuw veld is een persoonsgegeven. Antwoorden op zo'n veld komen nooit in logs, in de
auditlog, in exports, in berichten of in de wachtrij van medewerkers. Alleen de pagina van de
afspraak in de agenda toont ze, aan wie afspraken mag bekijken.

Kiest u **Geen persoonsgegeven**, dan moet u bevestigen dat de antwoorden nooit iets over een
persoon zeggen. Dat past bijvoorbeeld bij een keuzelijst met het onderwerp van het gesprek. Twijfelt u,
laat het dan een persoonsgegeven. Zet u een veld later terug naar persoonsgegeven, dan geldt dat
meteen ook voor de antwoorden die al gegeven zijn.

Hoe u een veld ook markeert: alle antwoorden worden gewist met de andere gegevens van de
afspraak, 24 uur na de afspraak of na het annuleren.

<!-- field: options -->
## Keuzes

Alleen bij een keuzelijst. Zet elke keuze op een eigen regel, als sleutel, Nederlandse tekst en
Engelse tekst, gescheiden door een puntkomma:

    huur; Huurtoeslag; Rent benefit
    zorg; Zorgtoeslag; Healthcare benefit

Hoogstens 30 keuzes, elk met een eigen sleutel.

<!-- field: minimum -->
## Grenzen

Alleen bij een geheel getal of een datum. Bij een getal zijn de grenzen de laagste en hoogste
waarde. Bij een datum zijn het dagen vanaf de dag van boeken: `-30` en `0` laten alleen een datum
in de afgelopen 30 dagen toe. Leeg is geen grens.

## Volgorde en uitzetten

Met **Omhoog** en **Omlaag** zet u de velden in de volgorde waarin de inwoner ze ziet. Met
**Uitzetten** verdwijnt een veld van het formulier. Antwoorden die al gegeven zijn blijven bij de
afspraak staan tot ze worden gewist, en tellen vanaf dat moment als persoonsgegeven.

Een formulier heeft hoogstens het aantal eigen velden dat de superuser instelt (standaard 5).

## Wat wordt vastgelegd

Elke wijziging komt in de auditlog met de sleutel van het veld en wat er veranderde, nooit de
tekst van een vraag of een antwoord.
