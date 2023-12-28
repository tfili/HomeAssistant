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

let status = Shelly.getComponentStatus("light",0);
updateKVP(status.output, status.brightness);

function updateLight(event) {
  const delta = event.delta;
  if (delta.source === "loopback" || typeof delta.brightness === "undefined") {
    return;
  }
  
  Shelly.call(
    "KVS.Get",
    {key: KEY},
    function (entry, error_code, message) {
      const kvp = JSON.parse(entry.value);
      
      if (DEBUG) {
        print("KVS.Get");
        print(JSON.stringify(kvp));
      }
      
      let newBrightness = delta.brightness;
      let newIsOn = delta.output;
      if (newIsOn === kvp.on && newBrightness !== kvp.brightness) {
        newBrightness = Math.floor(newBrightness * 0.75);
        Shelly.call("Light.Set",
          {"id":event.id, "on":newIsOn, "brightness":newBrightness},
          null);
      }
      updateKVP(newIsOn, newBrightness, kvp);
    }, null);
}

Shelly.addStatusHandler(updateLight, null);
