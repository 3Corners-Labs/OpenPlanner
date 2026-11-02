# Huisstijl

Op dit scherm bepaalt u hoe de afspraakpagina's van een organisatie eruitzien: naam en logo in
de kop, kleuren, lettertypen en de vorm van kaarten en knoppen. U opent het via
**Organisaties → organisatie → Huisstijl**. Alleen een superuser kan de huisstijl wijzigen; voor het opslaan
vraagt het portaal soms opnieuw om in te loggen. Het beheerportaal zelf houdt één vaste stijl.

Bovenaan ziet u een voorbeeld zoals de huisstijl nu is opgeslagen. Een wijziging geldt direct
voor alle groepen van de organisatie.

Vrije CSS uploaden kan niet. Met vrije CSS kun je labels verbergen, de focusrand weghalen of
gegevens naar een andere website laten lekken. De pagina's moeten toegankelijk (WCAG 2.2 AA),
veilig en privacyvriendelijk blijven, wat de huisstijl ook is. Daarom kiest u uit een vaste
lijst van instellingen, die het portaal controleert.

## Naam, kop en kleuren

<!-- field: display_name -->
### Naam in de kop

De naam die deelnemers bovenaan elke pagina zien. Laat het veld leeg om de naam van de
organisatie te tonen.

<!-- field: header_style -->
### Kop

- **Logo en naam, met een lijn in de accentkleur**: de standaard.
- **Logo en naam op een balk in de accentkleur**: de hele kop krijgt de accentkleur, de tekst
  de kleur bij "Tekst op de accentkleur".
- **Alleen het logo**: voor een logo waar de naam al in staat. De naam blijft beschikbaar
  voor schermlezers. Zonder logo toont de pagina gewoon logo en naam.

<!-- field: accent_color -->
### Accentkleur

Voor knoppen, links en de gekozen dag. De kleur moet een contrast van minstens 4,5:1 halen
met de tekst erop en met de achtergronden waarop ze als linkkleur staat.

<!-- field: color_text -->
### Kleuren

Kleuren kiest u met de kleurkiezer. Bij opslaan controleert het portaal alle paren die samen
op de pagina staan: tekst op de pagina, op kaarten en in velden minstens 4,5:1, randen van
invoervelden en de focusrand minstens 3:1. Lukt dat niet, dan noemt het portaal de kleur die
u het laatst hebt veranderd. Een donker thema kan niet: de gele focusmarkering vraagt donkere
tekst.

Wie in het besturingssysteem om meer contrast vraagt, krijgt de standaardkleuren met extra
contrast. Alleen de accentkleur blijft dan staan.

## Vorm en tekst

<!-- field: font_size -->
### Tekstgrootte

In pixels, van 16 tot 20. Kleiner dan de standaard van de browser kan niet; deelnemers die hun
browser groter zetten, krijgen de tekst ook groter.

<!-- field: radius -->
### Afronding

Hoe rond de hoeken van kaarten (0 tot 24 pixels) en van knoppen en velden (0 tot 16 pixels)
zijn. 0 geeft rechte hoeken.

De regelafstand (1,4 tot 2), de dikte van koppen, de maximale breedte (640 tot 1440 pixels)
en de schaduw onder kaarten stelt u hier ook in. Afstanden tussen onderdelen en de grootte
van knoppen staan vast: die zorgen dat de pagina met toetsenbord en vinger goed te bedienen
blijft.

## Logo

PNG, JPEG of WebP van hoogstens 200 kB. SVG kan niet, omdat een SVG-bestand scripts kan
bevatten. Een liggend logo van ongeveer 80 pixels hoog werkt het best.

## Lettertypen

U kunt drie lettertypen uploaden: voor gewone tekst, voor vette tekst en voor koppen. Alleen
WOFF2, hoogstens 250 kB per bestand. Het bestand moet een compleet, correct WOFF2-bestand zijn: de kop
en de tabellijst worden gecontroleerd, en een bestand dat een andere omvang opgeeft dan het
heeft, of dat uitgepakt groter wordt dan de limiet van het platform (standaard 2 MB, in te
stellen door de superuser bij de platforminstellingen), wordt geweigerd. Wordt een lettertype
dat elders wel werkt geweigerd, exporteer het dan opnieuw als WOFF2 met een gangbare
converter. Zonder vet lettertype maakt de browser de gewone letter
zelf vetter; zonder lettertype voor koppen gebruiken de koppen het tekstlettertype.

De lettertypen worden vanaf deze dienst geladen, nooit van een andere website zoals Google
Fonts, zodat de computer van een deelnemer geen contact zoekt met andere partijen. Zorg dat de
licentie van het lettertype het gebruik op een eigen server toestaat.

## Themabestand

Wie de huisstijl liever in een bestand bijhoudt, bijvoorbeeld voor meerdere omgevingen:

1. Klik op **Huidig thema downloaden**. U krijgt een CSS-bestand met alle instellingen;
   instellingen die op de standaard staan, staan er als commentaar in.
2. Pas de waarden aan. Haal de commentaartekens weg rond een regel die u wilt wijzigen.
3. Upload het bestand bij **Themabestand toepassen**.

<!-- field: theme_file -->
### Wat er in het bestand mag

Alleen regels als `--booking-radius: 4px;`, met een naam uit het gedownloade bestand en een
geldige waarde, eventueel binnen één `:root { ... }`. Commentaar mag. Selectors, `@import`,
`url()` en `!important` kunnen niet. Bij een fout noemt het portaal de regel. Het bestand
vervangt het hele thema: wat er niet in staat, krijgt weer de standaardwaarde, ook de
accentkleur. Logo en lettertypen blijven staan. Lettertypefamilies (`--booking-font-family`)
kunt u alleen via het bestand instellen.
