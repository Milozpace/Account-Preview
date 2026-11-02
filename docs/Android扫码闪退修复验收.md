# Android 扫码闪退修复验收

## 范围与原因

点击“开始扫码配对”后，Android 原生扫码线程触发未捕获空指针并退出。
已取得的真机日志在启动阶段先报 `ComponentDiscovery` 的
`NoSuchMethodException`，涉及 ML Kit 的 `CommonComponentRegistrar`、
`VisionCommonRegistrar`、`BarcodeRegistrar` 三个公开无参构造。

R8 full mode 保留了这些类名，却删除了通过反射调用的构造方法。
本地优化映射的 `usage.txt` 与旧 APK 的 DEX 均确认构造缺失；
相机权限已授予，错误发生在配对材料导入前。
故障手机上的内部候选为 0.4.1 / 4001；另行检查已发布
0.4.0-preview.2（0.4.2 / 4002）的 APK，确认同样缺失三个构造。

## 改动

- 仅为 ML Kit 的 `ComponentRegistrar` 实现保留公开无参构造，继续启用发布优化。
- `scripts/verify_android_apk.py` 使用 Android SDK `apkanalyzer` 检查最终 APK
  中的三个公开无参构造；检查缺失、工具错误均返回非零。
- Android CI 在 preview、personal 候选打包上传前运行此检查。
- 修复候选标识为 0.4.0-preview.3，内部安装版本 0.4.3 / 4003。
  本轮不创建 Release、标签或替换旧发布产物。

## 验证记录（2026-11-02）

- 修复前：对旧正式 APK 执行新增检查，三个构造均为 false，退出码 1。
- 前端 200 项、版本与构建档案 9 项测试通过，版本一致性检查通过。
- 修复后：新 APK 三个公开无参构造均为 true，退出码 0；发布优化保持开启。
- 前端与 Android 原生库重新编译；本机创建符号链接受限，复制新编译库后由
  Gradle `assembleArm64Release` 完成优化打包，校验包内库与构建中间产物一致。
- preview 签名与手机原安装包一致，已通过 `adb install -r` 覆盖到 0.4.3 / 4003。
  安装前后 UID、首次安装时间、数据目录一致，旧 APK 已留作本机恢复候选。
- 新进程启动成功，日志不再出现组件注册构造缺失或主线程崩溃。
- 已交给用户实际测试相机打开、退出和重复进入；结果待用户反馈。

## 验收方式

```text
python scripts/verify_android_apk.py 修复候选.apk
python scripts/product_version.py check
```

覆盖安装前后核对包名、签名、UID、首次安装时间与数据目录；只使用
`adb install -r`，不卸载、不清除数据、不更换安装身份。先确认启动不再出现
registrar 初始化异常，再由用户实际点击扫码，检查相机打开及重复进入。
若用户自行执行真实配对，其结果另记；静态 DEX 检查不等于配对全流程验收。
