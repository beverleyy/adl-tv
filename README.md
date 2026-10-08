# Overhead

My advisor is an airplane nut. I am an airplane nut. The lab had an unused TV and a bunch of ADSB data. What's a girl to do, right? 

This app is built to power the ADL TV, and ONLY the ADL TV, but if you have your own ADSB receiver, you can easily hook it up to this code and run your own TV. All you need is a [tar1090](https://github.com/wiedehopf/tar1090) / dump1090 receiver with a readable `aircraft.json`.

[Overhead on a 1080p TV](screenshot.png)

As a bonus, due to the extreme age of the ancient workstation we repurposed for this thingy, it will run with any Python >= 3.5 (yes! 3.5!). However it does require a fairly modern web browser.

[View a demonstrator with simulated traffic here!](https://beverleyy.github.io/adl-tv)

## Quick start (no receiver needed)

```sh
python3 tools/mock_receiver.py &
python3 server.py --receiver http://localhost:9099
```

Open [http://localhost:8081](http://localhost:8081). The mock receiver flies a handful of made-up aircraft around SFO.

## Using it with your receiver

The thingy reads user-specific information from a `config.json` file placed in the root. To add your receiver all you need to do is to pass your tar1090 page to the file. To do this, make a copy of `config.example.json`, rename it to `config.json`, and add your information to it. Then, start the server with
```sh
python3 server.py
```

The location is picked up directly from the receiver unless you specify your own coordinates (latitude and longitude). If your receiver doesn't publish a location, or it publishes an inaccurately approximate location, you'll need to set the `lat` and `lon` in the `config.json` manually.

|Setting|Default|What it does|
|-|-|-|
|`receiver`|`http://localhost:8080`|your tar1090 / dump1090 web page|
|`lat`, `lon`|from the receiver, else Stanford campus|where your antenna is. Set both to override what the receiver publishes|
|`lab\\\_name`, `subtitle`|`ADL`, `Live from the ADL rooftop`|labels on the map and in the corner|
|`carto\\\_key`|\*(none)\*|free \[CARTO](https://carto.com/basemaps/apikey/) key for the dark basemap; without one, OpenStreetMap tiles are darkened instead|
|`vis\\\_nm`, `min\\\_elev`, `vis\\\_max\\\_alt`|`2`, `10`, `7000`|what counts as "in sight"|
|`dwell`|`14`|seconds each aircraft is featured|
|`allow\\\_ga`|`false`|also feature general aviation aircrafts|
|`no\\\_fr24`|`false`|turn off the Flightradar24 feed (see below)|
|`host`, `port`|`127.0.0.1`, `8081`|where the dashboard is served|

Ah, one more thing. I centered the map at SFO/SJC/OAK because I go to Stanford and we have three big airports within an hour's drive from us. If you(r receiver) is somewhere else, you'll probably want to pick another airport, in which case, go to `static/js/dashboard.js` and edit `APTS` and `MAJOR`.

## Putting it on the TV

The manual setup way is to open a terminal, type 
```sh
python3 server.py
```
with relevant options, and then manually open the web browser and go to `localhost:8081`. However, because the campus is overly reliant on PG&E lines, an automated method is highly desirable. Cue the `start.sh` script. By running
```sh
bash start.sh --install
```
on any Ubuntu >=16.04 (yes, 16.04!), you can have the server start at every login and also open a full-screen Firefox browser to run the app. And of course, uninstalling is also pretty easy with the `--uninstall` option.

On Windows, macOS or newer Linux versions that aren't from 10 years ago, `python3 start.py --install` does the same with Chrome, Edge or Firefox. 

## The (not so) fine print

### THIS IS NOT MEANT TO BE RUN AS A PUBLIC WEBSITE!!! PLEASE READ!!!

### Not for navigation or any operational use!!!

### Where the data comes from

||Source|Notes|
|-|-|-|
|Aircraft positions|your receiver||
|Routes, types, registrations|[Flightradar24](https://www.flightradar24.com) website feed, [adsbdb](https://www.adsbdb.com), [VRS standing data](https://github.com/vradarserver/standing-data) (CC0)|tried in that order; each route is checked against the aircraft's position|
|Photos|[Planespotters.net](https://www.planespotters.net)|fetched by the browser; the photographer is credited on screen|
|Map|[CARTO](https://carto.com/attributions) or [OpenStreetMap](https://www.openstreetmap.org/copyright)|tiles cached on disk|

**About the FR24 feed:** I use [Flightradar24](https://www.flightradar24.com) to check arrivals/departures at each of the airports and match filed routes against the tail numbers. However, I don't have the balls to ask my advisor to expense a FR24 API subscription. The reader can draw their own conclusions as to what this means. In other unrelated news, FR24's terms don't permit automated access, so you can turn the FR24 sync off with `"no_fr24": true` in the `config.json` file. Everything falls back to the other sources if it's unavailable.

**Data privacy:** Code is entirely open-sourced, and none of your information is uploaded to any servers whatsoever (c'mon guys, I'm a broke grad student. Why would I spend money on a server to harvest data?) The dashboard page itself is necessarily centred on your receiver, so keep the server on `127.0.0.1` (the default) unless you're happy for people on your network to see where it is.

## Development

Literally do whatever. For ADL folks, you can literally run this from your laptop (ask me for the config file!) Claude built some unit tests, you can run them with

```sh
python3 -m unittest discover tests
```

## Credits

Built with \[Leaflet](https://leafletjs.com) (BSD-2-Clause) and the
\[Source Sans 3](https://github.com/adobe-fonts/source-sans) typeface (SIL OFL 1.1), both bundled in
`static/`. The color palette follows Stanford's identity colours. Map data © OpenStreetMap contributors.


