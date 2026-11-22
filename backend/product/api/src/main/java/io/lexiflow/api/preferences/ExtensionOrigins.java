package io.lexiflow.api.preferences;

/** 固定允许正式与测试扩展；不允许其他扩展或网页来源访问本机偏好。 */
final class ExtensionOrigins {
  private ExtensionOrigins() {}

  static boolean allowed(String origin) {
    return "chrome-extension://cgbdoaoalppcklmcbmkdogoagppcimkk".equals(origin)
        || "chrome-extension://mnngbjikfoafikclcilgkhhkmcnmlinm".equals(origin);
  }
}
