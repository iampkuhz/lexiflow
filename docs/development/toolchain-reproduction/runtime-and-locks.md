# 1. 工具链复现：运行时与锁定

## 1.1. 受控输入

复现使用仓库确定的 Java 25、Gradle Wrapper、依赖锁和构建配置。版本选择先依据官方兼容性事实审定，再固定为精确值；不接受系统默认 Java、全局 Gradle 或浮动版本作为替代。

## 1.2. 核对

```bash
python3 -m scripts.toolchain.java_gradle --version
cd backend && ./gradlew --version
```

记录实际运行时、Wrapper、锁定文件与任何环境差异。版本不符时停止并更新 ADR/OpenSpec；不要通过修改环境变量或删除锁文件掩盖差异。
