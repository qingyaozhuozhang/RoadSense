@echo off
setlocal
set APP_HOME=%~dp0
if not exist "%APP_HOME%gradle\wrapper\gradle-wrapper.jar" (
 echo Missing gradle-wrapper.jar. Run: powershell -ExecutionPolicy Bypass -File Setup-Windows.ps1
 exit /b 1
)
if defined JAVA_HOME (set JAVA_CMD=%JAVA_HOME%\bin\java.exe) else (set JAVA_CMD=java.exe)
"%JAVA_CMD%" -Xmx128m -Dorg.gradle.appname=gradlew -classpath "%APP_HOME%gradle\wrapper\gradle-wrapper.jar" org.gradle.wrapper.GradleWrapperMain %*
exit /b %ERRORLEVEL%
