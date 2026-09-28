package io.lexiflow.api.hints;

import io.lexiflow.observability.platform.StructuredEvent;
import io.lexiflow.observability.platform.StructuredEventLogger;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/** 为精确字幕提示入口生成后端请求身份，并负责唯一终态事件。 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 20)
public final class CaptionRequestObservationFilter extends OncePerRequestFilter {
  private static final String PATH = "/api/v1/caption-hints";
  private final StructuredEventLogger events;

  /**
   * 注入固定结构化事件发件器。
   *
   * @param events 失败隔离的事件输出适配器。
   */
  public CaptionRequestObservationFilter(StructuredEventLogger events) {
    this.events = events;
  }

  @Override
  protected boolean shouldNotFilter(HttpServletRequest request) {
    return !PATH.equals(request.getServletPath());
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain chain)
      throws ServletException, IOException {
    var observation = new CaptionRequestObservation(UUID.randomUUID(), System.nanoTime());
    response.setHeader("X-Request-ID", observation.requestId().toString());
    request.setAttribute(CaptionRequestObservation.ATTRIBUTE, observation);
    try {
      chain.doFilter(request, response);
    } catch (RuntimeException | ServletException | IOException failure) {
      observation.reason(StructuredEvent.Reason.INTERNAL_ERROR);
      if (!response.isCommitted()) {
        response.resetBuffer();
        response.setStatus(HttpServletResponse.SC_INTERNAL_SERVER_ERROR);
        response.setHeader("Cache-Control", "no-store");
        response.setContentType("application/json");
        response.setCharacterEncoding("UTF-8");
        response.getWriter().write("{\"error\":\"INTERNAL_ERROR\"}");
      }
    } finally {
      events.tryEmit(() -> observation.terminal(System.nanoTime(), response.getStatus()));
    }
  }
}
