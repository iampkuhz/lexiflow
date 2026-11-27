package io.lexiflow.api.preferences;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.fail;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class PreferencesNoStoreFilterTest {
  @Test
  void exactOriginPreflightIsNarrowAndNoStore() throws Exception {
    var filter = new PreferencesNoStoreFilter();
    var request = new MockHttpServletRequest("OPTIONS", "/api/v1/preferences");
    request.addHeader("Origin", "chrome-extension://cgbdoaoalppcklmcbmkdogoagppcimkk");
    request.addHeader("Access-Control-Request-Method", "POST");
    request.addHeader("Access-Control-Request-Headers", "content-type");
    var response = new MockHttpServletResponse();
    filter.doFilter(request, response, (req, res) -> fail("preflight must terminate in filter"));
    assertEquals(204, response.getStatus());
    assertEquals(
        "chrome-extension://cgbdoaoalppcklmcbmkdogoagppcimkk",
        response.getHeader("Access-Control-Allow-Origin"));
    assertEquals("POST", response.getHeader("Access-Control-Allow-Methods"));
    assertEquals("Content-Type", response.getHeader("Access-Control-Allow-Headers"));
    assertEquals("no-store", response.getHeader("Cache-Control"));
  }

  @Test
  void previewOriginCanPreflightRecentHints() throws Exception {
    var filter = new PreferencesNoStoreFilter();
    var request = new MockHttpServletRequest("OPTIONS", "/api/v1/recent-hints");
    var origin = "chrome-extension://mnngbjikfoafikclcilgkhhkmcnmlinm";
    request.addHeader("Origin", origin);
    request.addHeader("Access-Control-Request-Method", "POST");
    var response = new MockHttpServletResponse();
    filter.doFilter(request, response, (req, res) -> fail("preflight must terminate in filter"));
    assertEquals(204, response.getStatus());
    assertEquals(origin, response.getHeader("Access-Control-Allow-Origin"));
  }

  @Test
  void browseWithPersonalStateUsesTheSameOriginAndNoStorePolicy() throws Exception {
    var filter = new PreferencesNoStoreFilter();
    var request = new MockHttpServletRequest("POST", "/api/v1/lexicon");
    var denied = new MockHttpServletResponse();
    filter.doFilter(
        request, denied, (req, res) -> fail("browse without extension Origin must be denied"));
    assertEquals(403, denied.getStatus());
    var origin = "chrome-extension://mnngbjikfoafikclcilgkhhkmcnmlinm";
    request.addHeader("Origin", origin);
    var allowed = new MockHttpServletResponse();
    filter.doFilter(
        request,
        allowed,
        (req, res) -> assertEquals("no-store", allowed.getHeader("Cache-Control")));
    assertEquals(origin, allowed.getHeader("Access-Control-Allow-Origin"));
  }

  @Test
  void wrongOriginAndExtraHeadersAreDenied() throws Exception {
    var filter = new PreferencesNoStoreFilter();
    for (String origin : new String[] {"http://localhost", "chrome-extension://wrong"}) {
      var request = new MockHttpServletRequest("OPTIONS", "/api/v1/preferences");
      request.addHeader("Origin", origin);
      request.addHeader("Access-Control-Request-Method", "POST");
      var response = new MockHttpServletResponse();
      filter.doFilter(request, response, (req, res) -> fail("denied preflight must terminate"));
      assertEquals(403, response.getStatus());
      assertNull(response.getHeader("Access-Control-Allow-Origin"));
    }
  }
}
