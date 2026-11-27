package io.lexiflow.api.preferences;

/** 固定允许正式与测试扩展；不允许其他扩展或网页来源访问本机偏好。 */
public final class ExtensionOrigins {
  private ExtensionOrigins() {}

  /**
   * 判断请求来源是否精确匹配已配置的正式或测试 Chrome 扩展。 不接受网页来源、其他扩展或带额外路径的来源值。
   *
   * @param origin 含义：HTTP Origin 请求头。取值范围：正式或测试扩展的完整来源字符串，否则返回 false。
   * @return 来源获准访问本机偏好时为 true
   */
  public static boolean allowed(String origin) {
    return "chrome-extension://cgbdoaoalppcklmcbmkdogoagppcimkk".equals(origin)
        || "chrome-extension://mnngbjikfoafikclcilgkhhkmcnmlinm".equals(origin);
  }
}
