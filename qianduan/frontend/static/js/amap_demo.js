(() => {
  const map=window.createRoadSenseMap('demo-map',{zoom:18.5});
  const place=[116.397428,39.90923];  // Demonstration ONLY: WGS84
  document.getElementById('map-demo-center').addEventListener('click',()=>map.update(...place));
  document.getElementById('map-demo-points').addEventListener('click',()=>map.setPoints([
    {lon:116.397428,lat:39.90923,level:'严重',time:'示例 · 非真实记录'},
    {lon:116.4003,lat:39.9088,level:'中度',time:'示例 · 非真实记录'},
    {lon:116.395,lat:39.9076,level:'轻微',time:'示例 · 非真实记录'}
  ]));
  map.update(...place);
})();
