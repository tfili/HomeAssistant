<?php
$image_basename = 'latest_mop';
$image_latest = $image_basename.'.png';
$vacuum_log = 'http://127.0.0.1:8080/mop.log'; # Could also be HTTPS
$ha_rest980 = 'http://192.168.0.105:8123/api/states/sensor.rest980mop';
include "image.php"
?>