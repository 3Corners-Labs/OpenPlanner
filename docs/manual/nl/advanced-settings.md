# Geavanceerde instellingen

Dit scherm laat zien hoe de installatie is ingesteld en waar elke waarde vandaan komt. U opent
het via **Geavanceerde instellingen** in het menu. De superuser en de rol Compliance en privacy
kunnen het bekijken. De superuser kan een deel van de instellingen wijzigen; de auditor leest
alleen.

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

## Een instelling wijzigen

De superuser wijzigt de instellingen voor bedrijfsvoering, bewaartermijnen, opslag,
snelheidslimieten, sessies van het beheerportaal, medewerkers en de planner, de reservemailserver
en de updatecontrole. Webex en de inlogkoppeling volgen in een latere versie.

- Vul onder **Wijzigen** de nieuwe waarde in en kies **Opslaan**. Het portaal accepteert alleen
  waarden binnen zijn grenzen, bijvoorbeeld 1 tot 30 afspraken per uur per adres. Een waarde
  daarbuiten wordt bij de regel geweigerd, met de reden.
- Sommige instellingen hangen van elkaar af: kritieke opslag moet onder weinig opslag blijven, en
  de herinnering om opnieuw te boeken moet vóór het vervallen komen. Een waarde die een andere
  instelling tegenspreekt, wordt geweigerd.
- **Standaardwaarde gebruiken** haalt de hier ingestelde waarde weg; dan geldt weer de
  standaardwaarde (of de waarde van de server).
- Een instelling die **vastgezet is door de beheerder van de server** heeft geen formulier:
  wijzig die in de omgeving van de server, of vraag het de beheerder. **Beheer dit voortaan hier**
  neemt de waarde van de server over in het portaal; vraag de beheerder daarna de variabele die
  bij de regel staat weg te halen en te herstarten. Tot dan geldt de waarde van de server nog.
- **Wachtwoorden en tokens** (het wachtwoord van de mailserver, het leestoken van de
  updatecontrole) kunt u alleen schrijven: het scherm zegt alleen of er een is ingesteld. Laat het
  veld leeg om de huidige te houden; **Standaardwaarde gebruiken** haalt hem weg. Ze worden
  versleuteld opgeslagen en vragen de `SETTINGS_ENCRYPTION_KEY` van de server.
- Het **logniveau** valt altijd vanzelf terug naar de standaard, na hooguit 24 uur. Vul in na
  hoeveel uur. Persoonsgegevens blijven op elk niveau gemaskeerd in het log.

Voor een wijziging moet u kort geleden zijn ingelogd. Elke wijziging staat in het auditlog met
de oude en de nieuwe waarde; een geweigerde wijziging ook.

## De inlogkoppeling wijzigen

Alleen de superuser kan wijzigen welke claims van de identity provider de rollen, organisaties
en groepen dragen, en welke waarde welke rol is. Vul **Inlogkoppeling wijzigen** in en kies
**Testen met een nieuwe login**. U wordt naar de identity provider gestuurd om opnieuw in te
loggen. De nieuwe koppeling wordt op die login toegepast: hij wordt alleen bewaard als u er nog
superuser mee bent. Anders verandert er niets en meldt het scherm dat. Zo kan niemand per
ongeluk iedereen buitensluiten.

Is een waarde vastgezet door de beheerder van de server, dan is de hele koppeling van de
beheerder en staat het formulier er niet. Verandert de identity provider later een claim of
rolwaarde, dan kan de beheerder van de server de koppeling vanaf de server herstellen.

## Opmerkingen

- **Buiten de grenzen van het portaal**: de beheerder van de server heeft een waarde gezet die
  het portaal zelf niet zou accepteren, zoals een heel korte bewaartermijn. Die geldt wel, maar
  vraag de beheerder of dat zo bedoeld is.
- **Opgeslagen waarde voldoet niet aan de regels en wordt genegeerd**: een opgeslagen waarde is
  niet meer geldig, bijvoorbeeld omdat die buiten het portaal is aangepast. Dan geldt de
  standaardwaarde of de waarde van de server.
