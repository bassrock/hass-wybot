# hass-wybot

Allows controlling of WyBot pool vaccums as Vacumm entities in HomeAssistant

Note: This has only been tested with 1 S2 Pro with the docking station and requires the use of the Cloud API.

I believe the WyBot could be controllable using Bluetooth on HomeAssistant but it requires someone to map the bluetooth commands.

## Setting up a WyBot S2 Pro with a Dock

WyBots disconnect from WiFI when they are in the water. However if you have a dock, the dock will stay connected to WiFi and relay commands underwater to the WyBot. HOWEVER, the order you setup the Dock & Wybot matter. YOU MUST first setup the dock, and connect it to WiFi then pair your robot with it. There is no way to set the wifi on the Dock after you setup the WyBot.
