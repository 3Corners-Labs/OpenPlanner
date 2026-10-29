# Geavanceerde instellingen

Dit scherm laat zien hoe de installatie is ingesteld en waar elke waarde vandaan komt. U opent
het via **Geavanceerde instellingen** in het menu. De superuser en de rol Compliance en privacy
kunnen het bekijken. U kunt hier nog niets wijzigen; dat volgt in een latere versie.

Geheime waarden, zoals wachtwoorden en sleutels, worden nooit getoond: alleen of ze zijn
ingesteld.

## Systeemstatus

De instellingen die alleen de beheerder van de server zet, in de omgeving van de server of in
een bestand. Ze zijn nodig voordat het portaal of de database werkt, of ze horen bij de server
zelf. Per instelling ziet u of die is ingesteld en waar die vandaan komt, nooit de waarde.

Draait de installatie niet als productie (bijvoorbeeld een testomgeving), dan staat hier ook
wat productie zou weigeren: een ontbrekende sleutel of een adres zonder https. Productie start
nooit met een van deze punten.

De ondersteuning kan u om een schermafdruk van dit scherm vragen in plaats van de configuratie
van de server.

## Platforminstellingen

Onder de systeemstatus staat elke instelling van het platform per onderwerp, met de huidige
waarde en de bron:

- **Standaardwaarde**: de waarde waarmee de software wordt geleverd.
- **Beheerportaal**: een waarde die in het portaal is ingesteld.
- **Omgeving**: een waarde die de beheerder van de server heeft gezet. De instelling is dan
  **vastgezet door de beheerder van de server**, en het scherm noemt de variabele. Een waarde
  in het portaal geldt niet zolang die vastzetting er is.
- **Bestand**: een geheim dat de server uit een bestand leest.

Een gewijzigde waarde geldt binnen ongeveer tien seconden, zonder herstart.

## Opmerkingen

- **Buiten de grenzen van het portaal**: de beheerder van de server heeft een waarde gezet die
  het portaal zelf niet zou accepteren, zoals een heel korte bewaartermijn. Die geldt wel, maar
  vraag de beheerder of dat zo bedoeld is.
- **Opgeslagen waarde voldoet niet aan de regels en wordt genegeerd**: een opgeslagen waarde is
  niet meer geldig, bijvoorbeeld omdat die buiten het portaal is aangepast. Dan geldt de
  standaardwaarde of de waarde van de server.
