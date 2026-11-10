# -*- coding: utf-8 -*-
"""The look of Lucent Video's own menus: per menu item a card (icon + colour, in resources/media/menu/<card>.jpg) and a
short explanation for the skin's info line. Plain data, no Kodi imports: also read by the tool that draws the cards.

Key: "<action>" or "<action>:<name | type | kind>" of the item's plugin address."""

# colour families of the cards (dark, filmic): watching, films, series, Kompas, lists, search, tools, discover
TINTS = {"watch": "#4b2d6b", "film": "#5a3a22", "tv": "#1d3a5c", "kompas": "#3d2a66", "lists": "#1f4a3c",
         "search": "#23394d", "tools": "#3a3a40", "discover": "#5a2630"}

# key: (icon, tint, Dutch explanation, English explanation)
MENU = {
    # Home
    "list:continue": ("play", "watch", "Verder waar je gebleven was, films en afleveringen.", "Pick up where you left off."),
    "list:nextup": ("next", "watch", "De volgende aflevering van elke serie die je kijkt.", "The next episode of every series you watch."),
    "menu:movie": ("film", "film", "Trending, populair, genres, studio's en meer.", "Trending, popular, genres, studios and more."),
    "menu:tvshow": ("tv", "tv", "Trending, nieuwe seizoenen, de kalender en meer.", "Trending, new seasons, the calendar and more."),
    "kompasmenu": ("compass", "kompas", "Keuzes die bij jouw smaak passen.", "Picks that suit your taste."),
    "pick": ("dice", "kompas", "Kies wie er kijkt en waar je zin in hebt: drie voorstellen.", "Choose who watches and the mood: three suggestions."),
    "services": ("cast", "discover", "Wat er nieuw is op Netflix, Disney+, Prime Video en meer.", "What is new on Netflix, Disney+, Prime Video and more."),
    "traktmenu": ("list", "lists", "Je kijklijst, collectie en eigen lijsten.", "Your watchlist, collection and own lists."),
    "searchmenu": ("search", "search", "Films en series zoeken, ook met je telefoon.", "Search films and series, also with your phone."),
    "who": ("user", "tools", "Wissel van profiel.", "Switch profile."),
    "tools": ("gear", "tools", "Instellingen, bibliotheek, back-up en hulp.", "Settings, library, backup and help."),
    "setup": ("wand", "tools", "Stap voor stap: account, sleutels en bronnen.", "Step by step: account, keys and sources."),
    # Kompas
    "list:foryou": ("star", "kompas", "Gekozen op wat je eerder keek.", "Chosen from what you watched."),
    "kompas_page": ("compass", "kompas", "Bladeren door keuzes voor jou.", "Browse the picks for you."),
    "list:kompas_foryou": ("star", "kompas", "Gekozen op wat je eerder keek.", "Chosen from what you watched."),
    "list:kompas_because": ("spark", "kompas", "Lijkt op wat je net zag.", "Like what you just watched."),
    "list:kompas_gems": ("gem", "kompas", "Goed beoordeeld, maar weinig bekend.", "Well rated, little known."),
    "list:kompas_new": ("spark", "kompas", "Nieuw en passend bij je smaak.", "New and to your taste."),
    "list:kompas_trending": ("flame", "kompas", "Wat nu populair is en bij je past.", "Popular now and to your taste."),
    "list:kompas_saved": ("bookmark", "kompas", "Een rij die je zelf bewaard hebt.", "A row you saved."),
    "kompas_taste": ("heart", "kompas", "Wat Kompas over je smaak weet, en aanpassen.", "What Kompas knows about your taste."),
    # Films and series
    "list:trending_movie": ("flame", "film", "Waar iedereen het deze week over heeft.", "What everyone talks about this week."),
    "list:popular_movie": ("heart", "film", "De films die nu het meest bekeken worden.", "The most watched films right now."),
    "list:toprated_movie": ("star", "film", "De hoogst gewaardeerde films ooit.", "The highest rated films."),
    "list:nowplaying_movie": ("ticket", "film", "Nu in de bioscoop.", "In cinemas now."),
    "list:upcoming_movie": ("clock", "film", "Binnenkort in de bioscoop.", "Coming to cinemas."),
    "list:trending_tv": ("flame", "tv", "Waar iedereen het deze week over heeft.", "What everyone talks about this week."),
    "list:popular_tv": ("heart", "tv", "De series die nu het meest bekeken worden.", "The most watched series right now."),
    "list:new_tv": ("spark", "tv", "Series die net begonnen zijn.", "Series that just started."),
    "list:toprated_tv": ("star", "tv", "De hoogst gewaardeerde series ooit.", "The highest rated series."),
    "list:airing_tv": ("broadcast", "tv", "Series waarvan nu nieuwe afleveringen komen.", "Series with new episodes now."),
    "list:upcoming_tv": ("clock", "tv", "Series die binnenkort beginnen.", "Series starting soon."),
    "list:calendar": ("calendar", "tv", "Nieuwe afleveringen van je series, per dag.", "New episodes of your series, by day."),
    "list:new_seasons": ("megaphone", "tv", "Nieuwe seizoenen die aangekondigd zijn.", "New seasons that were announced."),
    "genres": ("tag", "discover", "Kies een genre.", "Choose a genre."),
    "studios": ("building", "discover", "Studio's en netwerken.", "Studios and networks."),
    "decades": ("hourglass", "discover", "Per decennium, van de jaren '50 tot nu.", "By decade."),
    "awards": ("trophy", "discover", "Winnaars van de Oscars, Emmy's en meer.", "Oscar, Emmy and other winners."),
    "search:movie": ("search", "search", "Zoek een film.", "Search a film."),
    "search:tvshow": ("search", "search", "Zoek een serie.", "Search a series."),
    # My lists
    "list:coming_watchlist": ("hourglass", "lists", "Van je kijklijst, nog niet uit: met de datum.", "From your watchlist, not out yet."),
    "list:tonight": ("moon", "lists", "Klaargezet voor vanavond: start meteen.", "Ready for tonight: starts at once."),
    "list:watchlist_movie": ("bookmark", "lists", "Films op je kijklijst.", "Films on your watchlist."),
    "list:watchlist_tv": ("bookmark", "lists", "Series op je kijklijst.", "Series on your watchlist."),
    "list:collection_movie": ("box", "lists", "Films in je Trakt-collectie.", "Films in your Trakt collection."),
    "list:collection_tv": ("box", "lists", "Series in je Trakt-collectie.", "Series in your Trakt collection."),
    "traktlists:mine": ("list", "lists", "De lijsten die je zelf gemaakt hebt.", "The lists you made."),
    "traktlists:liked": ("heart", "lists", "Lijsten van anderen die je leuk vindt.", "Lists of others you like."),
    "traktlists:trending": ("flame", "lists", "Lijsten die nu populair zijn.", "Lists that are popular now."),
    "traktlists:popular": ("heart", "lists", "De populairste lijsten.", "The most popular lists."),
    "traktlists:search": ("search", "lists", "Zoek een lijst.", "Search a list."),
    "publiclists": ("globe", "lists", "Lijsten van andere Trakt-gebruikers.", "Lists of other Trakt users."),
    "mdblistmenu": ("list", "lists", "Je lijsten op MDBList.", "Your lists on MDBList."),
    "mdblists:mine": ("list", "lists", "De lijsten die je zelf gemaakt hebt.", "The lists you made."),
    "mdblists:liked": ("heart", "lists", "Lijsten van anderen die je leuk vindt.", "Lists of others you like."),
    "mdblists:top": ("trophy", "lists", "De populairste lijsten op MDBList.", "The top lists on MDBList."),
    "mdblists:search": ("search", "lists", "Zoek een lijst.", "Search a list."),
    "floppylists": ("list", "lists", "Je lijsten op Floppy.", "Your lists on Floppy."),
    # Search
    "remote": ("phone", "search", "Typ op je telefoon en stuur het naar de tv.", "Type on your phone and send it to the TV."),
    "search": ("search", "search", "Opnieuw zoeken.", "Search again."),
    # Settings and help
    "settings": ("gear", "tools", "Alle instellingen van Lucent Video.", "All settings of Lucent Video."),
    "subs_setup": ("cc", "tools", "Ondertitels, gesproken taal en AI-vertaling.", "Subtitles, spoken language and AI translation."),
    "skip_setup": ("skip", "tools", "Intro's vanzelf overslaan met TheIntroDB.", "Skip intros by itself with TheIntroDB."),
    "ranking": ("sort", "tools", "Welke bronnen bovenaan komen.", "Which sources come first."),
    "library_sync": ("refresh", "tools", "Je Trakt-collectie nu in de Kodi-bibliotheek zetten.", "Put your Trakt collection in the Kodi library now."),
    "library_choose": ("books", "tools", "Kies welke titels in de bibliotheek komen.", "Choose which titles go in the library."),
    "year": ("chart", "tools", "Je jaar in films en series.", "Your year in films and series."),
    "backup": ("cloud", "tools", "Instellingen en lijsten bewaren of terugzetten.", "Save or restore settings and lists."),
    "report": ("bug", "tools", "Een rapport zonder sleutels of adressen, voor hulp.", "A report without keys or addresses."),
    "clear_cache": ("trash", "tools", "Opgeslagen lijsten opnieuw ophalen.", "Fetch stored lists again."),
    "page": ("arrow", "tools", "De volgende pagina.", "The next page."),
}

# lists without their own entry: the card of their kind
FALLBACK = {"list": ("list", "discover"), "service": ("cast", "discover"), "traktlists": ("list", "lists"),
            "mdblists": ("list", "lists"), "seasons": ("tv", "tv")}


def cards():
    """{card name: (icon, tint)} of every card that has to exist."""
    out = {}
    for key, (icon, tint, _nl, _en) in MENU.items():
        out[card_name(icon, tint)] = (icon, tint)
    for icon, tint in FALLBACK.values():
        out[card_name(icon, tint)] = (icon, tint)
    return out


def card_name(icon, tint):
    return "%s_%s" % (tint, icon)
