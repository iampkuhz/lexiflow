package io.lexiflow.api.preferences;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/** 仅为偏好端点校验固定扩展来源并处理 CORS 预检。 对所有偏好响应设置 no-store，其他 API 路径不受此过滤器影响。 */
@Component
public final class PreferencesNoStoreFilter extends OncePerRequestFilter {

  /** 判断是否为需要偏好专属防护的 API 路径。 */
  @Override
  protected boolean shouldNotFilter(HttpServletRequest request) {
    return !"/api/v1/preferences".equals(request.getRequestURI())
        && !"/api/v1/recent-hints".equals(request.getRequestURI())
        && !"/api/v1/lexicon".equals(request.getRequestURI());
  }

  /** 为偏好请求校验来源、设置缓存头并处理预检。 */
  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain chain)
      throws ServletException, IOException {
    response.setHeader("Cache-Control", "no-store");
    response.addHeader("Vary", "Origin");
    String origin = request.getHeader("Origin");
    if (ExtensionOrigins.allowed(origin)) response.setHeader("Access-Control-Allow-Origin", origin);
    if ("OPTIONS".equals(request.getMethod())) {
      if (!ExtensionOrigins.allowed(origin)
          || !"POST".equals(request.getHeader("Access-Control-Request-Method"))) {
        response.sendError(HttpServletResponse.SC_FORBIDDEN);
        return;
      }
      String requested = request.getHeader("Access-Control-Request-Headers");
      if (requested != null && !requested.equalsIgnoreCase("content-type")) {
        response.sendError(HttpServletResponse.SC_FORBIDDEN);
        return;
      }
      response.setHeader("Access-Control-Allow-Methods", "POST");
      response.setHeader("Access-Control-Allow-Headers", "Content-Type");
      response.setStatus(HttpServletResponse.SC_NO_CONTENT);
      return;
    }
    if (!ExtensionOrigins.allowed(origin)) {
      response.sendError(HttpServletResponse.SC_FORBIDDEN);
      return;
    }
    chain.doFilter(request, response);
  }
}
