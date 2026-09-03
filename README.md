# **InGameRank is now deprecated!**
InGameRank has been ported to a [Hebnix](https://hebnix.com) plugin.

You can download the plugin from the Hebnix client itself, or from the [plugin page](https://hebnix.com/plugins/332)

Hebnix is open source, and the repository can be viewed [here](https://github.com/hebbins/Hebnix-Public)

If you run into any issues or need assistance, please join the [Discord Server](https://discord.gg/yr6xXb5wQd)

This repository will stay available for archive purposes only.

## InGameRank Overlay

Overlay for Rocket League, since after EAC update bakkesmod is not usable online anymore, so i made my own version.

## Acknowledgements & Credits
* **Visual Assets:** Massive thanks to [@BenTheDan](https://github.com/BenTheDan) for [BenTheDan/IngameRank](https://github.com/BenTheDan/IngameRank) repository for providing the clean rank, division, and playlist icons used in this project!
* **StatsAPI** This program makes use of the new stats api that RL put out for us devs read StatsAPI.md if you are interested or visit the official docs [here](https://www.rocketleague.com/en/developer/stats-api).

## BEFORE INSTALL
Edit your |RL Install Dir| \TAGame\Config\DefaultStatsAPI.ini find field PacketSendRate and set value to 20.
## Latest release 
[HERE](https://github.com/nixvio64/InGameRank/releases/latest)

##  Disclaimer
**Use at your own risk.** This application utilizes a transparent screen overlay to display information. While it merely reads network traffic and does not hook into or modify game memory, third-party overlays can occasionally be flagged by Easy Anti-Cheat. User discretion is strictly advised.

## Build yourself
Run pip install -r requirements.txt and run pyinstaller main.spec, if you want the packeged installer you can also run [this](NSI_BUILDER/installer.nsi) NSI installer script.

# Why does it have to show ranked data too?
I didn't find a way to detect the current gamemode (if Casual or Ranked) and i wanted to be able to see casual mmr and best competitive mmr in the default modes, i know it's not pretty but i might change in future adding more customizability.

## Todo:
Streak tracking (if requested), total matches this season 

## Contributing
Do you want to make this app even better? Contributions are highly encouraged and appreciated.
