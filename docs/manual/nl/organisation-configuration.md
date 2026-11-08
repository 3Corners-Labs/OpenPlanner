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
