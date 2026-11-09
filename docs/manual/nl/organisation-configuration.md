# Configuratie: de organisatie als JSON-document

Op dit scherm exporteert u de configuratie van één organisatie als JSON-bestand en leest u zo'n
bestand weer in. U opent het via **Organisaties → organisatie → Overzicht → Configuratie**. Het
is voor organisatiebeheerders en de superuser, en elke wijziging vraagt een recente login.

Een document is makkelijk met de hand aan te passen en blijft geldig tussen versies: objecten
worden met hun eigen sleutel genoemd (een groep met haar adresnaam, een provider met zijn
sleutel), nooit met nummers. Het bevat alleen configuratie: geen boekingen, geen deelnemers en
geen medewerkers. U kunt het in versiebeheer bewaren, een waarde in een editor aanpassen en
het weer inlezen, of een team van een testomgeving naar productie kopiëren.

## Exporteren

Kies **Document exporteren** om de configuratie van de organisatie te downloaden. Het bestand
bevat alleen wat u zelf op deze installatie zou mogen wijzigen: objecten en velden waarvoor u
een recht mist, blijven weg.

Vink **Geheimen opnemen** aan om wachtwoorden en sleutels mee te nemen. Ze worden verzegeld met
het wachtwoord dat u opgeeft; dat telt minstens 16 tekens en wordt twee keer gevraagd. Het
wachtwoord wordt nergens bewaard: zonder dat wachtwoord zijn de geheimen in het bestand
waardeloos en kan niemand het voor u terughalen. Zonder vinkje bevat het bestand geen enkel
geheim en is geen wachtwoord nodig.

## Een nieuwe omgeving opzetten

Met een export mét geheimen zet u een basisomgeving klaar zonder alles opnieuw in te typen:
koppelingen (SMTP, SMS, Webex en contactcenter met hun adressen), organisaties, groepen,
teksten en regels.

1. Exporteer op de bronomgeving met **Geheimen opnemen** en een wachtwoord (minstens 16
   tekens). Bewaar het wachtwoord apart van het bestand.
2. Lees het bestand in op de nieuwe omgeving, met hetzelfde wachtwoord: in het portaal onder
   **Geavanceerde instellingen → Configuratiepakket**, op de host met
   `python -m app.cli config apply config.json --passphrase-file pass.txt`, of met de
   installer (`scripts/install.sh --configuration config.json --passphrase-file pass.txt`).

U hebt geen aparte tool nodig om het bestand te openen: OpenPlanner ontsleutelt elk geheim zelf
bij het inlezen en bewaart het onder de eigen sleutel van de omgeving. In het bestand ziet u
een `seal`-blok en waarden als `{"sealed": "..."}`; een export zonder geheimen heeft die niet.
Een geheim kunt u ook met de hand in het bestand zetten, als `{"plain": "…"}`; OpenPlanner
verzegelt het bij het toepassen. Is de beheerder van de host akkoord, dan kan de superuser ook
exporteren zonder versleuteling (*Geavanceerde instellingen → Configuratiepakket*).

## Een document inlezen

Kies het bestand en **Plan tonen**. Er verandert nog niets. Het plan noemt per object wat er
zou worden aangemaakt, gewijzigd of verwijderd en welke velden, nooit de waarden. Lees het
voordat u doorgaat:

- Een **weigering** (bijvoorbeeld een waarde buiten de grenzen, een object dat nog in gebruik
  is of een recht dat u mist) stopt het hele document; verbeter het bestand en lees het opnieuw
  in.
- Een **waarschuwing** noemt wat extra aandacht verdient: verwijderen kan niet ongedaan worden
  gemaakt, en een andere claimwaarde (de sleutel van een organisatie of groep, of de
  claimwaarde van een rol) sluit wie op de oude waarde matcht buiten tot de identiteitsprovider
  de nieuwe waarde stuurt.
- Een document met verzegelde geheimen vraagt bij het toepassen opnieuw om zijn wachtwoord.

Kies **Plan toepassen** om alles in één keer te schrijven, of niets als een onderdeel mislukt.
Het plan dat u toepast is het plan dat u las: is de configuratie intussen veranderd, of stond
de pagina langer open dan de bewaartijd toestaat, lees het bestand dan opnieuw in. De processen
nemen de nieuwe instellingen binnen tien seconden over. Elke toegepaste wijziging staat in het
auditlog, zonder waarden.

Een document van een andere organisatie, of een dat het hele platform beschrijft, wordt hier
geweigerd; het document van het platform staat onder **Geavanceerde instellingen →
Configuratiepakket**.
