import asyncio
import os.path

@service(supports_response="only")
async def safe_snapshot(entity_id=None, filename=None, max_retries=5):
    output = {
        "result": False,
        "filename": filename,
        "attempts": 0
    }
    if entity_id is not None and filename is not None:
        service_data = {"entity_id": entity_id, "filename": filename}

        success = False
        count = 0
        while count < max_retries and not success:
            try:
                await hass.services.async_call("camera", "snapshot", service_data)
                success = os.path.isfile(filename)
            except:
                log.error("Snapshot failed")
            
            await asyncio.sleep(1)
            count += 1

        output["result"] = success
        output["attempts"] = count

    return output