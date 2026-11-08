# Opnamelijst schermafdrukken

Elke schermafdruk in de handleidingen heeft hier een id. Schrijvers gebruiken alleen deze id's
(`![alt](img/<id>.png "Bijschrift")`); de opname levert `img/<id>.png`. Ontbreekt een bestand,
dan toont de bouwstraat het kader "Schermafdruk volgt".

## Omgeving en spelregels

- Lokale installatie, gebouwd uit de broncode: deelnemerspagina's `http://127.0.0.1:8010/g/demo`,
  beheerportaal `http://127.0.0.1:8010/admin/`, inloggen via de ontwikkel-identity-provider
  (`127.0.0.1:8090`), e-mail in Mailpit (`127.0.0.1:8035`). Geen Webex Meetings op deze
  installatie.
- Inloggen met de ontwikkelgebruikers: `super` (applicatiebeheerder), `orgadmin`
  (organisatiebeheerder), `groupadmin` (planner), `agent` (medewerker), `auditor`
  (compliance en privacy).
- Alleen echte gebruikerspaden: boeken via de pagina, bevestigen via de link in de e-mail die de
  software stuurde, instellen via de schermen. Nooit de database of een testadres.
- Verzonnen gegevens: Sanne Jansen (`sanne.jansen@voorbeeld.nl`), Ahmed el Amrani
  (`ahmed.elamrani@voorbeeld.nl`), Pieter de Bakker van Bakkerij De Korenaar
  (`pieter@dekorenaar.voorbeeld.nl`).
- Venster 1280 × 800, schaal 2; mobiel 390 × 844. PNG.
- De productnaam wordt in beeld afgedekt met een effen grijze balk. Waar het kan, gaat hij weg
  via een echte instelling (zoals de voettekst "Afspraken via …" op de deelnemerspagina's).
  Elke afdekking staat in `bewijs/bewijslog.md`.

## Voorbereiding (zelf ook vastleggen)

1. Als `orgadmin`: zet de groep `demo` op de **eigen wachtrij** (groepsinstellingen) als dat nog
   niet zo is; leg vast wat er stond.
2. Zet de voettekst met de productnaam op de deelnemerspagina's uit, via de instelling die daarvoor
   bestaat.
3. Als `groupadmin`: geef vandaag een **afwijkende bezetting** met een eigen tijdblok van over
   ongeveer een uur tot drie uur later, 2 medewerkers, aanbod **Afspraken**, zodat er vandaag
   geboekt kan worden. (Dit is ook opname `pl-05`.)

4. Laad de voorbeeldgegevens met `scripts/voorbeeldgegevens.py`: verzonnen deelnemers boeken,
   bevestigen, verplaatsen en annuleren in de komende weken; een planner verplaatst en annuleert er
   een paar; daarna maakt `usage-backfill` de rapportcijfers bij. Alles via de echte schermen en
   e-mails, met de echte datum.

## {{Eindgebruikers}} (`bb-`)

| id | Scherm en toestand |
|---|---|
| bb-01-groepspagina | `/g/demo`: titel en maandkalender met vrije dagen |
| bb-02-tijden | dezelfde pagina na het kiezen van een dag: de vrije tijden |
| bb-03-formulier | formulier **Uw gegevens** met het gekozen moment ernaast, leeg |
| bb-04-foutmelding | formulier na **Afspraak maken** met een ongeldig e-mailadres: **Er is een probleem** |
| bb-05-controleer-mail | **Bevestig uw afspraak** met "We hebben u een e-mail gestuurd" |
| bb-06-mail-bevestigen | de e-mail "Bevestig uw afspraak met …", zoals de deelnemer hem ziet (HTML-deel) |
| bb-07-bevestigpagina | de pagina achter de link: knop **Afspraak bevestigen** |
| bb-08-bevestigd | **Uw afspraak is bevestigd** |
| bb-09-mail-bevestiging | de e-mail "Bevestiging van uw videogesprek op …" |
| bb-10-uw-afspraak | **Uw afspraak**: moment, groep, status, links om te wijzigen |
| bb-11-verplaatsen | **Afspraak verplaatsen** met de kalender |
| bb-12-nieuw-moment | **Nieuw moment bevestigen** (oud en nieuw moment) |
| bb-13-verplaatst | **Uw afspraak is verplaatst** |
| bb-14-annuleren | **Afspraak annuleren** met **Ja, annuleren** |
| bb-15-geannuleerd | **Uw afspraak is geannuleerd** |
| bb-16-mobiel | `/g/demo` op een telefoon (390 breed) |
| bb-17-mail-verplaatst | de e-mail "Uw videogesprek is verplaatst naar …" |
| bb-18-mail-geannuleerd | de e-mail "Uw videogesprek op … is geannuleerd" |
| bb-20-deelnemen | **Testomgeving met Webex Meetings:** de deelnemer opent de link en komt in het gesprek |
| bb-21-proefgesprek | **Testomgeving met Webex Meetings:** het proefgesprek om camera en microfoon te testen |

## Medewerker (`mw-`), ingelogd als `agent`

| id | Scherm en toestand |
|---|---|
| mw-01-overzicht | **Overzicht** zoals een medewerker het ziet (wachtrij vooraan) |
| mw-02-wachtrij | **Groepen → demo → Agenda → Wachtrij** met de afspraak van vandaag, status **Nog niet begonnen** |
| mw-03-opgepakt | na **Volgende oppakken**: de opgepakte afspraak met **Deelnemen aan het gesprek** |
| mw-04-vrijgegeven | na **Vrijgeven**: de afspraak staat weer in de wachtrij |
| mw-05-mijn-rechten | **Mijn rechten** van de medewerker |
| mw-06-profielmenu | het geopende profielmenu (weergave, taal, meldingen, uitloggen) |
| mw-07-mijn-sessies | **Mijn rechten → Mijn sessies** |
| mw-20-gesprek | **Testomgeving met Webex Meetings:** de medewerker in het gesprek |

## Planner (`pl-`), ingelogd als `groupadmin`

| id | Scherm en toestand |
|---|---|
| pl-01-groep-overzicht | **Groepen → demo → Overzicht** |
| pl-02-bezetting | **Planning → Bezetting**: de week met tijdblokken |
| pl-03-tijdblok-toevoegen | het formulier **Tijdblok toevoegen**, ingevuld vóór opslaan |
| pl-04-splitsen | het formulier **Splitsen** van een tijdblok (bijvoorbeeld lunch, 0 medewerkers) |
| pl-05-afwijkende-bezetting | **Planning → Afwijkende bezetting** met de afwijking van vandaag |
| pl-06-feestdagen | **Planning → Feestdagen** |
| pl-07-agenda-dag | **Agenda**, dagoverzicht met bevestigde afspraken |
| pl-08-agenda-week | **Weekoverzicht** |
| pl-09-afspraak-details | **Details** van een afspraak (verloop, achtergrondtaken) |
| pl-10-verplaatsen | **Verplaatsen** door de planner: tijdslot kiezen |
| pl-11-annuleren | **Annuleren** door de planner: de vaste redenen |
| pl-12-nieuwe-tijd | **Om nieuwe tijd vragen** |
| pl-13-tekort | een tijdslot met **Tekort: 1** in de agenda |
| pl-14-noodsluiting | **Noodsluiting** van de groep: het formulier, niet uitgevoerd |
| pl-15-gebruik | **Overzicht → Gebruik** (rapport van de groep) |
| pl-16-berichtteksten | **Berichten → Berichtteksten** |
| pl-17-wacht-op-nieuwe-tijd | de lijst **Wacht op nieuwe tijd** |

## Beheerder (`bh-`), ingelogd als `orgadmin`, of `super` waar vermeld

| id | Scherm en toestand |
|---|---|
| bh-01-overzicht | **Overzicht** (`super`): widgets, meldingen, systeemstatus |
| bh-02-organisatie | **Organisaties → demo → Overzicht** |
| bh-03-org-instellingen | **Overzicht → Instellingen** van de organisatie |
| bh-04-teksten | **Deelnemers → Teksten** van de organisatie |
| bh-05-huisstijl | **Deelnemers** (huisstijl) van de organisatie (`super`) |
| bh-06-kanalen | **Deelnemers → Kanalen** (`super`) |
| bh-07-groepen | **Groepen**: de lijst |
| bh-08-groep-nieuw | **Nieuwe groep**: het formulier, niet opgeslagen |
| bh-09-groepsinstellingen | **Groepen → demo → Instellingen**, met de eigen wachtrij gekozen |
| bh-10-boekingsformulier | **Instellingen → Boekingsformulier** |
| bh-11-herinneringen | **Berichten** (herinneringen) |
| bh-12-medewerkers | **Medewerkers**: de lijst |
| bh-13-medewerker | de pagina van één medewerker |
| bh-14-rechtenmatrix | **Rechtenmatrix** |
| bh-15-eigen-rol | **Rechtenmatrix → Rollen aanpassen → Nieuwe rol** (`super`), niet opgeslagen |
| bh-16-mijn-rechten | **Mijn rechten** van de organisatiebeheerder |
| bh-17-auditlog | **Auditlog** |
| bh-18-bewaartermijnen | **Bewaartermijnen** |
| bh-19-insluiten | **Overzicht → Insluiten** van de organisatie |
| bh-20-feestdagen-org | **Feestdagen** van de organisatie |
| bh-21-noodsluiting-org | **Noodsluiting** van de organisatie: het formulier, niet uitgevoerd |
| bh-22-integraties | **Integraties** (`super`): alleen het deel van Webex Meetings |
| bh-23-systeemstatus | **Geavanceerde instellingen** (`super`): systeemstatus |
| bh-24-platforminstellingen | **Geavanceerde instellingen → Instellingen** (`super`) |
| bh-25-updates | **Updates** (`super`) |
| bh-26-onderhoud | **Onderhoud** (`super`) |
| bh-27-logbestemmingen | **Logbestemmingen** (`super`) |
| bh-28-idp-koppeling | **Koppeling identiteitsprovider** |
| bh-29-inloggen-org | **Overzicht → Inloggen** van de organisatie |
| bh-30-handleiding | **Handleiding** in het portaal (ingebouwde hulp) |
| bh-31-contactalternatieven | **Contact** van de organisatie |
| bh-32-compliance-overzicht | **Overzicht** zoals `auditor` (compliance en privacy) het ziet |
