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
