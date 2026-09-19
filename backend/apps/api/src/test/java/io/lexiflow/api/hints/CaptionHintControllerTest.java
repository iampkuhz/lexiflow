package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.web.server.ResponseStatusException;

@SpringBootTest
class CaptionHintControllerTest {
  @Autowired private CaptionHintController controller;

  @Test
  void returnsChineseHintWithoutReplacingEnglishCaption() {
    var caption = "We need reliable captions.";

    var response =
        controller.hint(
            new CaptionHintRequest(
                UUID.fromString("00000000-0000-0000-0000-000000000001"),
                1,
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                caption,
                0,
                caption.length()));

    assertEquals(caption, response.caption());
    assertEquals("READY", response.state());
    assertEquals("可靠的", response.hints().getFirst().chineseGloss());
  }

  @Test
  void rejectsOutOfRangeCaptionRequest() {
    var request =
        new CaptionHintRequest(
            UUID.fromString("00000000-0000-0000-0000-000000000001"),
            1,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "caption",
            0,
            8);

    var exception = assertThrows(ResponseStatusException.class, () -> controller.hint(request));

    assertEquals(400, exception.getStatusCode().value());
  }
}
