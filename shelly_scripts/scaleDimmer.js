const KEY = "dimmerState";
const DEBUG = false;

function updateKVP(on, brightness, oldValues) {
  if (typeof oldValues === "undefined" || oldValues.on !== on || oldValues.brightness !== brightness) {
    const kvp = {
      key: KEY,
      value: JSON.stringify({
        on: on,
        brightness: brightness
      })
    };
    
    if (DEBUG) {
      print("KVS.Set");
      print(JSON.stringify(kvp));
    }
    
    Shelly.call(
      "KVS.Set",
      kvp
    );
  }
}

function updateLight(event) {
  const delta = event.delta;
  if (delta.source === "loopback" || typeof delta.brightness === "undefined") {
    return;
  }
  
  Shelly.call(
    "KVS.Get",
    {key: KEY},
    function (entry) {
      const kvp = JSON.parse(entry.value);
      
      if (DEBUG) {
        print("KVS.Get");
        print(JSON.stringify(kvp));
      }
      
      let newBrightness = delta.brightness;
      const newIsOn = delta.output;
      if (newBrightness !== kvp.brightness) {
        newBrightness = Math.floor(newBrightness * 0.75);
        Shelly.call("Light.Set",
          {"id":event.id, "on":newIsOn, "brightness":newBrightness},
          null);
      }
      updateKVP(newIsOn, newBrightness, kvp);
    }, null);
}

const lightStatus = Shelly.getComponentStatus("light",0);
updateKVP(lightStatus.output, lightStatus.brightness);

Shelly.addStatusHandler(updateLight, null);
