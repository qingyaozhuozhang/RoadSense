package com.roadsense.inspection

import android.Manifest
import android.app.AlertDialog
import android.content.*
import android.content.ContentValues
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.*
import android.provider.MediaStore
import android.view.Gravity
import android.view.View
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** RoadSense Android inspection client. */
class MainActivity : ComponentActivity() {
    private val cloud = BuildConfig.CLOUD_BASE_URL.trimEnd('/')
    private val ui = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()
    private val checking = AtomicBoolean(false)
    private var foreground = false
    private var screen = "offline"
    private lateinit var root: FrameLayout
    private lateinit var dashboard: View
    private lateinit var loginWeb: WebView
    private lateinit var offlineWeb: WebView
    private lateinit var mapWeb: WebView
    private var mapLoaded = false
    private var authCsrf = ""
    private var selectedDeviceId = ""
    private var lastDeviceRefreshAt = 0L
    private var loggedUsername = ""
    private lateinit var deviceSpinner: Spinner
    private lateinit var deviceInfo: TextView
    private var pickerLoading = false
    private var lastLat: Double? = null
    private var lastLon: Double? = null
    private var lastAccuracy: Double? = null
    private var previewId = ""
    private var cameraUri: Uri? = null
    private var uploadingPhoto = false
    private lateinit var statusLine: TextView
    private lateinit var connectionTag: TextView
    private lateinit var longitude: TextView
    private lateinit var latitude: TextView
    private lateinit var accuracy: TextView
    private lateinit var lastUpload: TextView
    private lateinit var startBtn: Button
    private lateinit var stopBtn: Button
    private lateinit var photoStatus: TextView
    private lateinit var photoImage: ImageView
    private lateinit var photoLevel: TextView
    private lateinit var photoCount: TextView
    private lateinit var archiveBtn: Button

    private val colors = object {
        val navy = Color.rgb(11,35,64)
        val primary = Color.rgb(25,120,213)
        val cyan = Color.rgb(20,182,182)
        val ink = Color.rgb(24,54,80)
        val muted = Color.rgb(104,130,153)
        val page = Color.rgb(243,248,253)
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density + 0.5f).toInt()
    private fun shape(color: Int, radius: Int = 18, stroke: Int? = null): GradientDrawable =
        GradientDrawable().apply {
            setColor(color); cornerRadius = dp(radius).toFloat()
            if (stroke != null) setStroke(dp(1), stroke)
        }
    private fun blend(a: Int, b: Int, radius: Int = 20) =
        GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(a,b)).apply { cornerRadius = dp(radius).toFloat() }
    private fun text(value: String, size: Float, color: Int, bold: Boolean = false) = TextView(this).apply {
        this.text = value; textSize = size; setTextColor(color)
        if (bold) setTypeface(Typeface.DEFAULT,Typeface.BOLD)
        includeFontPadding = false
    }
    private fun col() = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
    private fun row() = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
    private fun add(parent: LinearLayout, child: View, top: Int = 0, bottom: Int = 0) {
        parent.addView(child, LinearLayout.LayoutParams(-1,-2).apply {
            topMargin=dp(top); bottomMargin=dp(bottom)
        })
    }
    private fun card(): LinearLayout = col().apply {
        setPadding(dp(19), dp(20), dp(19), dp(20))
        background=shape(Color.WHITE,22,Color.rgb(225,235,246))
        elevation=dp(3).toFloat()
    }
    private fun button(value:String, primary:Boolean=true):Button = Button(this).apply {
        text=value; textSize=14f; isAllCaps=false
        setTextColor(if(primary) Color.WHITE else colors.ink)
        background=if(primary) blend(colors.primary,colors.cyan,14) else shape(Color.rgb(232,242,251),14)
        minHeight=dp(49); setPadding(dp(8),0,dp(8),0)
        stateListAnimator=null
    }
    private fun metric(title:String, value:TextView): LinearLayout = col().apply {
        background=shape(Color.rgb(244,250,255),14,Color.rgb(221,235,245))
        setPadding(dp(14),dp(14),dp(10),dp(14))
        add(this,text(title,11f,colors.muted),0,9);add(this,value)
    }
    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        val fine = result[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
            ContextCompat.checkSelfPermission(this,Manifest.permission.ACCESS_FINE_LOCATION)==PackageManager.PERMISSION_GRANTED
        if(fine) launchTracking() else statusLine.text="精确定位未授权，请在系统设置中开启"
    }
    private val photoPicker = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if(result.resultCode == RESULT_OK) {
            val uri = result.data?.data ?: cameraUri
            if(uri!=null) inspectPhoto(uri)
        }
        cameraUri=null
    }
    private val updates = object: BroadcastReceiver() {
        override fun onReceive(context:Context?, intent:Intent?) {
            val raw=intent?.getStringExtra(LocationUploadService.EXTRA_PAYLOAD)?:return
            try {
                val payload=JSONObject(raw)
                statusLine.text=payload.optString("state","定位中")
                if(payload.has("latitude") && payload.has("longitude")) {
                    val lat=payload.getDouble("latitude"); val lon=payload.getDouble("longitude")
                    if(lat in -90.0..90.0 && lon in -180.0..180.0) {
                        lastLat=lat;lastLon=lon
                        latitude.text=String.format(java.util.Locale.US,"%.6f",lat)
                        longitude.text=String.format(java.util.Locale.US,"%.6f",lon)
                        if(payload.has("accuracy")) {
                            lastAccuracy=payload.optDouble("accuracy");
                            accuracy.text=String.format(java.util.Locale.US,"± %.1f 米",lastAccuracy)
                        }
                        updateMap()
                    }
                }
                if(payload.optBoolean("uploaded",false)) lastUpload.text=java.text.SimpleDateFormat("HH:mm:ss",java.util.Locale.CHINA).format(java.util.Date())
            } catch (_:Exception) {}
            updateButtons()
        }
    }
    private val serverProbe=object:Runnable {
        override fun run() {
            if(!foreground || isDestroyed || isFinishing) return
            checkCloud()
            if(screen=="dashboard" && System.currentTimeMillis()-lastDeviceRefreshAt>20000L){
                lastDeviceRefreshAt=System.currentTimeMillis()
                fetchDeviceList() // Server assignment always wins over stale local UI.
            }
            ui.postDelayed(this,5000L)
        }
    }

    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window,false)
        window.statusBarColor=colors.navy; window.navigationBarColor=colors.navy
        root=FrameLayout(this).apply {setBackgroundColor(colors.page)}
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars=insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val keyboard=insets.getInsets(WindowInsetsCompat.Type.ime())
            view.setPadding(0,bars.top,0,maxOf(bars.bottom,keyboard.bottom))
            insets
        }
        dashboard=buildDashboard()
        loginWeb=WebView(this).apply {
            settings.javaScriptEnabled=true;settings.domStorageEnabled=true
            settings.allowFileAccess=false;settings.allowContentAccess=false
            settings.mixedContentMode=WebSettings.MIXED_CONTENT_NEVER_ALLOW
            webViewClient=object:WebViewClient(){
                override fun onPageFinished(view:WebView?,url:String?) {
                    if(url?.contains("/app/ready")==true) fetchAuth()
                    else if (url != null && !url.contains("/login") && !url.contains("/register") &&
                             !url.contains("/forgot-password") && url.startsWith(cloud)) fetchAuth()
                }
                override fun shouldOverrideUrlLoading(view:WebView?,request:WebResourceRequest):Boolean {
                    val link=request.url
                    val base=Uri.parse(cloud)
                    if(link.scheme==base.scheme&&link.host==base.host&&link.port==base.port) return false
                    try {startActivity(Intent(Intent.ACTION_VIEW,link))}catch (_:Exception){}
                    return true
                }
            }
        }
        CookieManager.getInstance().setAcceptCookie(true)
        offlineWeb=WebView(this).apply {
            settings.javaScriptEnabled=true;settings.domStorageEnabled=true
            settings.allowFileAccess=false
        }
        root.addView(dashboard,FrameLayout.LayoutParams(-1,-1))
        root.addView(loginWeb,FrameLayout.LayoutParams(-1,-1))
        root.addView(offlineWeb,FrameLayout.LayoutParams(-1,-1))
        setContentView(root)
        ViewCompat.requestApplyInsets(root)
        showOffline()
        if(!getPreferences(MODE_PRIVATE).getBoolean("privacy_accepted",false)) showPrivacy()
    }

    private fun buildDashboard():View {
        val scroll=ScrollView(this).apply {isFillViewport=true;clipToPadding=false;setBackgroundColor(colors.page)}
        val body=col().apply {setPadding(dp(18),dp(20),dp(18),dp(36))}
        val header=row()
        val mark=ImageView(this).apply {setImageResource(R.mipmap.ic_launcher);scaleType=ImageView.ScaleType.CENTER_CROP}
        header.addView(mark,LinearLayout.LayoutParams(dp(45),dp(45)))
        val brand=col().apply {setPadding(dp(11),0,0,0)}
        add(brand,text("ROADSENSE",20f,colors.ink,true));add(brand,text("道路智能巡检平台",10f,colors.muted),5)
        header.addView(brand,LinearLayout.LayoutParams(0,-2,1f))
        connectionTag=text("●  已连接",11f,colors.cyan,true)
        header.addView(connectionTag)
        add(body,header,0,18)

        val hero=col().apply {setPadding(dp(21),dp(23),dp(21),dp(22));background=blend(colors.navy,Color.rgb(17,73,109),24)}
        add(hero,text("ROAD · INTELLIGENCE",10f,Color.rgb(101,217,228),true),0,13)
        add(hero,text("道路智能巡检平台",25f,Color.WHITE,true),0,12)
        add(hero,text("高德原生定位 · 实时云端同步\n现场拍照识别 · 确认后正式归档",13f,Color.rgb(180,216,239)))
        statusLine=text("等待定位",13f,Color.rgb(120,239,215),true)
        val statusWrap=col().apply {setPadding(dp(13),dp(13),dp(12),dp(13));background=shape(Color.rgb(28,85,116),13)}
        add(statusWrap,statusLine)
        add(hero,statusWrap,18)
        add(body,hero,0,18)

        val deviceCard=card();add(deviceCard,text("当前登录账号与巡检设备",18f,colors.ink,true),0,9)
        deviceInfo=text("正在验证账号与设备绑定…",12f,colors.muted)
        add(deviceCard,deviceInfo,0,9)
        deviceSpinner=Spinner(this)
        add(deviceCard,deviceSpinner)
        add(deviceCard,text("手机定位信息关联当前登录账号；已分配巡检设备时，可选择对应设备。",11f,colors.muted),9)
        val switchAccount=button("退出 / 切换账号",false)
        switchAccount.setOnClickListener {
            if(LocationUploadService.running)stopTracking()
            selectedDeviceId="";authCsrf="";loggedUsername="";previewId=""
            lastLat=null;lastLon=null;lastAccuracy=null;mapLoaded=false
            longitude.text="--.------";latitude.text="--.------"
            accuracy.text="等待定位";lastUpload.text="尚未同步"
            photoImage.setImageResource(R.drawable.road_photo_placeholder)
            photoStatus.text="请在新账号下选择照片并进行识别"
            photoLevel.text="";photoCount.text="";archiveBtn.visibility=View.GONE
            mapWeb.loadUrl("about:blank")
            renderScreen("login")
            loginWeb.loadUrl("$cloud/login?fresh=1&next=/app/ready")
        }
        add(deviceCard,switchAccount,12)
        add(body,deviceCard,0,17)

        val gps=card();add(gps,text("01   实时定位",19f,colors.ink,true),0,7)
        add(gps,text("高德原生定位 · WGS84 原始坐标",12f,colors.muted),0,16)
        val coords=row()
        longitude=text("--.------",18f,colors.ink,true)
        latitude=text("--.------",18f,colors.ink,true)
        coords.addView(metric("经度  LONGITUDE",longitude),LinearLayout.LayoutParams(0,-2,1f).apply {rightMargin=dp(7)})
        coords.addView(metric("纬度  LATITUDE",latitude),LinearLayout.LayoutParams(0,-2,1f).apply {leftMargin=dp(7)})
        add(gps,coords)
        val accuracyRow=row().apply {setPadding(0,dp(15),0,dp(14))}
        accuracy=text("等待卫星定位",12f,colors.muted)
        lastUpload=text("尚未同步",12f,colors.muted)
        accuracyRow.addView(accuracy,LinearLayout.LayoutParams(0,-2,1f))
        accuracyRow.addView(lastUpload)
        add(gps,accuracyRow)
        val buttons=row()
        startBtn=button("开始定位")
        stopBtn=button("停止定位",false)
        buttons.addView(startBtn,LinearLayout.LayoutParams(0,dp(51),1f).apply {rightMargin=dp(6)})
        buttons.addView(stopBtn,LinearLayout.LayoutParams(0,dp(51),1f).apply {leftMargin=dp(6)})
        startBtn.setOnClickListener{beginTracking()};stopBtn.setOnClickListener{stopTracking()}
        add(gps,buttons)
        add(gps,text("定位启动后自动同步到当前登录账号；目标同步间隔 0.5 秒。",11f,colors.muted),13)
        add(body,gps,0,17)

        val mapCard=card();add(mapCard,text("02   实时位置地图",19f,colors.ink,true),0,7)
        add(mapCard,text("高德道路地图 · 根据原生坐标更新",12f,colors.muted),0,14)
        mapWeb=WebView(this).apply {
            settings.javaScriptEnabled=true;settings.domStorageEnabled=true
            settings.allowFileAccess=false;settings.allowContentAccess=false
            settings.mixedContentMode=WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setBackgroundColor(Color.rgb(232,243,249))
            background=shape(Color.rgb(232,243,249),16)
            webViewClient=object:WebViewClient(){
                override fun onPageFinished(view:WebView?,url:String?){
                    mapLoaded=url?.contains("/app/map")==true
                    updateMap()
                }
                override fun onReceivedError(view:WebView?,request:WebResourceRequest?,error:android.webkit.WebResourceError?){
                    if (request?.isForMainFrame == true) {
                        mapLoaded=false
                        statusLine.text="地图页面加载失败，请检查云端 /app/map"
                    }
                }
            }
        }
        mapCard.addView(mapWeb,LinearLayout.LayoutParams(-1,dp(255)))
        add(body,mapCard,0,17)

        val photoCard=card();add(photoCard,text("03   AI 道路拍照识别",19f,colors.ink,true),0,7)
        add(photoCard,text("拍照后自动发送云端 YOLO，检查结果后确认归档。",12f,colors.muted),0,15)
        photoImage=ImageView(this).apply {
            setBackgroundColor(Color.rgb(240,248,253));scaleType=ImageView.ScaleType.FIT_CENTER
            setImageResource(R.drawable.road_photo_placeholder)
            setPadding(0,0,0,0)
        }
        photoImage.background=shape(Color.rgb(235,245,251),14)
        photoImage.clipToOutline=true
        photoCard.addView(photoImage,LinearLayout.LayoutParams(-1,dp(205)))
        add(photoCard,text("道路巡检概念示意图 · 尚未拍照",11f,colors.muted),9,3)
        val photoButtons=row().apply {setPadding(0,dp(13),0,0)}
        val camera=button("拍摄路面照片")
        val album=button("从相册选择",false)
        photoButtons.addView(camera,LinearLayout.LayoutParams(0,dp(50),1f).apply {rightMargin=dp(6)})
        photoButtons.addView(album,LinearLayout.LayoutParams(0,dp(50),1f).apply {leftMargin=dp(6)})
        camera.setOnClickListener{selectPhoto(true)};album.setOnClickListener{selectPhoto(false)}
        add(photoCard,photoButtons)
        photoStatus=text("拍照或选择照片后自动识别；检查结果并点击「确认归档」才保存。",12f,colors.muted)
        add(photoCard,photoStatus,13)
        photoLevel=text("",15f,colors.ink,true);add(photoCard,photoLevel,12)
        photoCount=text("",12f,colors.muted);add(photoCard,photoCount,8)
        archiveBtn=button("确认归档到云端").apply {visibility=View.GONE;isEnabled=false;setOnClickListener{archivePhoto()}}
        add(photoCard,archiveBtn,14)
        add(body,photoCard,0,17)

        val foot=col()
        add(foot,text("RoadSense · MOBILE FIELD NODE",11f,colors.muted,true))
        add(foot,text("硬件 GPS 优先，手机定位为备用来源",11f,colors.muted),7)
        add(body,foot,9)
        scroll.addView(body)
        updateButtons()
        return scroll
    }

    private fun renderScreen(next:String) {
        screen=next
        dashboard.visibility=if(next=="dashboard") View.VISIBLE else View.GONE
        loginWeb.visibility=if(next=="login") View.VISIBLE else View.GONE
        offlineWeb.visibility=if(next=="offline") View.VISIBLE else View.GONE
        if(next=="offline") offlineWeb.onResume() else offlineWeb.onPause()
    }
    private fun showOffline(){
        // Server is unavailable: display ONLY the offline game. Do not leave the
        // native reporting UI or the Android foreground-upload notification above it.
        // After reconnection the user can explicitly restart reporting.
        if(LocationUploadService.running) {
            stopService(Intent(this, LocationUploadService::class.java))
        }
        val alreadyVisible=screen=="offline" && offlineWeb.url!=null
        renderScreen("offline")
        if(alreadyVisible) return
        val html=try{assets.open("offline_runner.html").bufferedReader().use{it.readText()}}
                 catch(_:Exception){"<html><body>服务器暂时不可用</body></html>"}
        offlineWeb.loadDataWithBaseURL(null,html,"text/html","UTF-8",null)
    }
    private fun showLogin(){
        if(screen=="login")return
        renderScreen("login")
        loginWeb.loadUrl("$cloud/login?next=/app/ready")
    }
    private fun showDashboard(){
        val wasDashboard=screen=="dashboard"
        renderScreen("dashboard")
        connectionTag.text="●  云端已连接"
        statusLine.text=if(LocationUploadService.running) "定位服务运行中" else "云端已连接，点击开始定位"
        if(!wasDashboard || !mapLoaded){mapLoaded=false;mapWeb.loadUrl("$cloud/app/map")}
        fetchDeviceList()
        updateButtons()
    }
    private fun fetchDeviceList(){
        if(pickerLoading)return
        pickerLoading=true
        io.execute {
            try {
                val data=http("/api/devices?scope=owned")
                val items=data.optJSONArray("devices") ?: org.json.JSONArray()
                val ids=mutableListOf<String>()
                val labels=mutableListOf<String>()
                for(i in 0 until items.length()) {
                    val item=items.optJSONObject(i)?:continue
                    ids.add(item.optString("device_id"))
                    labels.add(item.optString("name")+"（"+item.optString("device_id")+"）")
                }
                ui.post {
                    if(isDestroyed)return@post
                    val previously=selectedDeviceId
                    deviceSpinner.onItemSelectedListener=null
                    deviceSpinner.adapter=ArrayAdapter(this,android.R.layout.simple_spinner_dropdown_item,labels)
                    val index=ids.indexOf(previously).let { if(it>=0)it else 0 }
                    selectedDeviceId=ids.getOrNull(index)?:""
                    if(ids.isNotEmpty())deviceSpinner.setSelection(index)
                    deviceInfo.text="账号：$loggedUsername  ·  ${if(ids.isEmpty()) "暂无所属巡检设备" else "已绑定 ${ids.size} 台设备"}"
                    deviceSpinner.onItemSelectedListener=object:android.widget.AdapterView.OnItemSelectedListener {
                        override fun onNothingSelected(parent:android.widget.AdapterView<*>?){}
                        override fun onItemSelected(parent:android.widget.AdapterView<*>?,view:View?,position:Int,id:Long){
                            val choice=ids.getOrNull(position)?:return
                            if(selectedDeviceId!=choice){
                                if(LocationUploadService.running)stopTracking()
                                selectedDeviceId=choice
                                previewId="";archiveBtn.visibility=View.GONE
                                mapLoaded=false
                                mapWeb.loadUrl("$cloud/app/map?device_id="+Uri.encode(choice))
                                statusLine.text="已切换巡检设备，请重新开始定位"
                            }
                            updateButtons()
                        }
                    }
                    if(previously!=selectedDeviceId){
                        if(LocationUploadService.running)stopTracking()
                        mapLoaded=false
                        mapWeb.loadUrl(if(selectedDeviceId.isNotBlank()) "$cloud/app/map?device_id="+Uri.encode(selectedDeviceId) else "$cloud/app/map")
                    }
                    updateButtons()
                }
            }catch(e:Exception){ui.post{if(::deviceInfo.isInitialized)deviceInfo.text="读取设备列表失败：${e.message}"}}
            finally{pickerLoading=false}
        }
    }

    private fun checkCloud(){
        if(!checking.compareAndSet(false,true))return
        io.execute{
            var ok=false
            try{
                val connection=(URL("$cloud/api/health").openConnection() as HttpURLConnection)
                connection.connectTimeout=2400;connection.readTimeout=2400
                try{ok=connection.responseCode==200} finally{connection.disconnect()}
            }catch(_:Exception){}
            checking.set(false)
            ui.post{
                if(!foreground||isFinishing||isDestroyed)return@post
                if(!ok) showOffline()
                else if(screen=="offline") fetchAuth()
            }
        }
    }
    private fun cookie():String=CookieManager.getInstance().getCookie(cloud)?:""
    private fun http(path:String,method:String="GET",contentType:String?=null,bytes:ByteArray?=null,csrf:String=""):JSONObject {
        val c=(URL("$cloud$path").openConnection() as HttpURLConnection)
        c.connectTimeout=9000;c.readTimeout=35000;c.requestMethod=method;c.instanceFollowRedirects=false
        c.setRequestProperty("Accept","application/json")
        c.setRequestProperty("X-RoadSense-Mobile","android")
        val cookie=cookie();if(cookie.isNotBlank())c.setRequestProperty("Cookie",cookie)
        if(contentType!=null)c.setRequestProperty("Content-Type",contentType)
        if(csrf.isNotBlank())c.setRequestProperty("X-CSRF-Token",csrf)
        try{
            if(bytes!=null){c.doOutput=true;c.outputStream.use{it.write(bytes)}}
            val status=c.responseCode
            val body=(if(status in 200..299)c.inputStream else c.errorStream)?.bufferedReader()?.use{it.readText()} ?: "{}"
            val data=try{JSONObject(body)}catch(_:Exception){JSONObject()}
            if(status==401)ui.post { if(screen=="dashboard") showLogin() }
            if(status !in 200..299)throw IllegalStateException(data.optString("message","云端请求错误 HTTP $status"))
            return data
        }finally{c.disconnect()}
    }
    private fun fetchAuth(){
        io.execute{
            var logged=false;var csrf=""
            try{
                val data=http("/api/auth/me")
                logged=!data.isNull("user") && data.optJSONObject("user")!=null
                loggedUsername=data.optJSONObject("user")?.optString("username","")?:""
                csrf=data.optString("csrf_token","")
            }catch(_:Exception){}
            ui.post{
                if(isFinishing||isDestroyed)return@post
                if(logged){authCsrf=csrf;showDashboard()} else showLogin()
            }
        }
    }
    private fun showPrivacy(){
        AlertDialog.Builder(this).setTitle("高德定位与隐私说明")
            .setMessage("RoadSense 在您点击开始定位后，使用高德 Android 定位 SDK 采集精确位置，向您的阿里云 RoadSense 服务器发送坐标。定位服务运行时显示前台通知，您可随时停止。拍照识别需要上传照片至云端。\n\n高德隐私政策：https://lbs.amap.com/pages/privacy/")
            .setPositiveButton("同意"){_,_->getPreferences(MODE_PRIVATE).edit().putBoolean("privacy_accepted",true).apply()}
            .setNegativeButton("暂不同意"){_,_->statusLine.text="未同意定位隐私说明"}
            .show()
    }
    private fun beginTracking(){
        // Keep both normal buttons visible; permission is checked when the user taps Start.
        if(LocationUploadService.running){statusLine.text="正在定位";return}
        io.execute {
            try {
                val permission=http("/api/mobile/permission")
                ui.post {
                    if(isDestroyed || isFinishing)return@post
                    if(!permission.optBoolean("allowed",false)){
                        statusLine.text="当前账号暂无定位权限，请联系管理员开通"
                        Toast.makeText(this,"当前账号暂无定位权限，请联系管理员开通",Toast.LENGTH_LONG).show()
                    } else if(!getPreferences(MODE_PRIVATE).getBoolean("privacy_accepted",false)) {
                        showPrivacy()
                    } else if(ContextCompat.checkSelfPermission(this,Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED) {
                        permissions.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION,Manifest.permission.ACCESS_COARSE_LOCATION))
                    } else launchTracking()
                }
            }catch(e:Exception){ui.post{statusLine.text="无法确认账号定位权限：${e.message}"}}
        }
    }
    private fun launchTracking(){
        ContextCompat.startForegroundService(this,Intent(this,LocationUploadService::class.java).setAction(LocationUploadService.ACTION_START)
            .putExtra(LocationUploadService.EXTRA_DEVICE_ID,selectedDeviceId))
        statusLine.text="正在启动高德定位…"
        ui.postDelayed({updateButtons()},900)
    }
    private fun stopTracking(){
        stopService(Intent(this,LocationUploadService::class.java))
        statusLine.text="定位已停止";ui.postDelayed({updateButtons()},350)
    }
    private fun updateButtons(){
        if(!::startBtn.isInitialized)return
        // No disabled/greyed-out buttons: show the two regular actions at all times.
        startBtn.isEnabled=true
        stopBtn.isEnabled=true
        startBtn.alpha=1f
        stopBtn.alpha=1f
    }
    private fun updateMap(){
        if(!::mapWeb.isInitialized || !mapLoaded)return
        val lat=lastLat?:return;val lon=lastLon?:return
        mapWeb.evaluateJavascript("window.nativeUpdateMap && window.nativeUpdateMap($lon,$lat);",null)
    }
    private fun selectPhoto(camera:Boolean){
        if(uploadingPhoto)return
        try{
            if(camera){
                val data=ContentValues().apply{
                    put(MediaStore.Images.Media.DISPLAY_NAME,"roadsense_${System.currentTimeMillis()}.jpg")
                    put(MediaStore.Images.Media.MIME_TYPE,"image/jpeg")
                }
                cameraUri=contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI,data)
                val capture=Intent(MediaStore.ACTION_IMAGE_CAPTURE)
                if(cameraUri!=null)capture.putExtra(MediaStore.EXTRA_OUTPUT,cameraUri)
                photoPicker.launch(capture)
            }else photoPicker.launch(Intent(Intent.ACTION_GET_CONTENT).apply{type="image/*";addCategory(Intent.CATEGORY_OPENABLE)})
        }catch(e:Exception){photoStatus.text="无法打开相机/相册：${e.message}"}
    }
    private fun encodePhoto(uri:Uri):ByteArray{
        val opts=BitmapFactory.Options().apply{inJustDecodeBounds=true}
        contentResolver.openInputStream(uri).use{BitmapFactory.decodeStream(it,null,opts)}
        var sample=1;while(maxOf(opts.outWidth,opts.outHeight)/sample>1800)sample*=2
        val bitmapOptions=BitmapFactory.Options().apply{inSampleSize=sample}
        val original=contentResolver.openInputStream(uri).use{BitmapFactory.decodeStream(it,null,bitmapOptions)}
            ?:throw IllegalArgumentException("无法读取手机照片")
        val maxSide=maxOf(original.width,original.height)
        val ratio=minOf(1f,1600f/maxSide)
        val bmp=if(ratio<1f) Bitmap.createScaledBitmap(original,(original.width*ratio).toInt().coerceAtLeast(1),(original.height*ratio).toInt().coerceAtLeast(1),true) else original
        if(bmp!==original)original.recycle()
        val output=ByteArrayOutputStream();bmp.compress(Bitmap.CompressFormat.JPEG,84,output);bmp.recycle()
        if(output.size()>8*1024*1024)throw IllegalArgumentException("照片大于 8MB，请选小一点的图片")
        return output.toByteArray()
    }
    private fun inspectPhoto(uri:Uri){
        uploadingPhoto=true;previewId="";archiveBtn.visibility=View.GONE
        photoLevel.text="";photoCount.text="";photoStatus.text="正在压缩照片并执行云端 YOLO 识别…"
        io.execute{
            try{
                val photo=encodePhoto(uri)
                val selectedBitmap=BitmapFactory.decodeByteArray(photo,0,photo.size)
                if(selectedBitmap!=null) ui.post { photoImage.setImageBitmap(selectedBitmap) }
                val boundary="RoadSense${UUID.randomUUID()}"
                val body=ByteArrayOutputStream()
                fun line(value:String){body.write(value.toByteArray(Charsets.UTF_8))}
                for((key,value) in listOf("latitude" to lastLat?.toString(),"longitude" to lastLon?.toString())){
                    if(value!=null){line("--$boundary\r\nContent-Disposition: form-data; name=\"$key\"\r\n\r\n$value\r\n")}
                }
                line("--$boundary\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"mobile.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n")
                body.write(photo);line("\r\n--$boundary--\r\n")
                val data=http("/api/phone/preview?device_id="+Uri.encode(selectedDeviceId),"POST","multipart/form-data; boundary=$boundary",body.toByteArray(),authCsrf)
                val id=data.optString("preview_id","")
                if(id.isBlank())throw IllegalStateException("服务器未返回预览编号")
                val rec=data.optJSONObject("record")?:JSONObject()
                val image=data.optString("result_image_data_url","")
                val bitmap=if(image.startsWith("data:image/") && image.contains(',')){
                    val raw=android.util.Base64.decode(image.substringAfter(','),android.util.Base64.DEFAULT)
                    BitmapFactory.decodeByteArray(raw,0,raw.size)
                }else null
                ui.post{
                    previewId=id;uploadingPhoto=false
                    if(bitmap!=null)photoImage.setImageBitmap(bitmap)
                    photoLevel.text="识别结果：${rec.optString("level","未知")}";
                    photoCount.text="检测目标 ${rec.optInt("detection_count",0)} 处  ·  损伤占比 ${String.format(java.util.Locale.US,"%.2f",rec.optDouble("damage_ratio",0.0)*100)}%"
                    archiveBtn.visibility=View.VISIBLE;archiveBtn.isEnabled=true
                    photoStatus.text="识别完成。点击「确认归档」后才会保存正式记录。"
                }
            }catch(e:Exception){ui.post{uploadingPhoto=false;photoStatus.text="识别失败：${e.message}"}}
        }
    }
    private fun archivePhoto(){
        val id=previewId;if(id.isBlank())return
        archiveBtn.isEnabled=false;photoStatus.text="正在保存正式巡检档案…"
        io.execute{
            try{
                val body=JSONObject().put("preview_id",id).toString().toByteArray(Charsets.UTF_8)
                val data=http("/api/phone/archive?device_id="+Uri.encode(selectedDeviceId),"POST","application/json; charset=utf-8",body,authCsrf)
                if(!data.optBoolean("archived",false))throw IllegalStateException(data.optString("message","云端尚未确认归档"))
                ui.post{previewId="";archiveBtn.visibility=View.GONE;photoStatus.text="✓ 已归档成功，可在云端损伤记录档案查看"}
            }catch(e:Exception){ui.post{archiveBtn.isEnabled=true;photoStatus.text="归档失败：${e.message}。可以重试。"}}
        }
    }
    override fun onStart(){
        super.onStart()
        val filter=IntentFilter(LocationUploadService.ACTION_UPDATE)
        if(Build.VERSION.SDK_INT>=33)registerReceiver(updates,filter,Context.RECEIVER_NOT_EXPORTED)
        else {@Suppress("DEPRECATION") registerReceiver(updates,filter)}
        foreground=true;updateButtons();ui.removeCallbacks(serverProbe);ui.post(serverProbe)
        if(screen=="dashboard")fetchDeviceList()
    }
    override fun onStop(){
        foreground=false;ui.removeCallbacks(serverProbe)
        unregisterReceiver(updates)
        super.onStop()
    }
    @Deprecated("Back navigation") override fun onBackPressed(){
        if(screen=="login"&&loginWeb.canGoBack())loginWeb.goBack()
        else super.onBackPressed()
    }
    override fun onDestroy(){
        ui.removeCallbacks(serverProbe);io.shutdownNow()
        mapWeb.destroy();loginWeb.destroy();offlineWeb.destroy()
        super.onDestroy()
    }
}
