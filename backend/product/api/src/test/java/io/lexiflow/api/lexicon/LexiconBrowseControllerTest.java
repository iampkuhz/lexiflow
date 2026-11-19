package io.lexiflow.api.lexicon;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.domain.model.LexiconBrowseEntry;
import io.lexiflow.lexicon.domain.model.LexiconBrowsePage;
import io.lexiflow.lexicon.domain.model.PublishedHintState;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

class LexiconBrowseControllerTest {
  @Test
  void normalizesExactQueryAndReturnsCanonicalLemmaAndPublishedState() {
    var repository = mock(LexiconReadRepository.class);
    when(repository.browse("alias", true, 1, 20))
        .thenReturn(
            new LexiconBrowsePage(
                List.of(
                    new LexiconBrowseEntry(
                        42, "canonical", "释义", 81L, PublishedHintState.ELIGIBLE)),
                1,
                1,
                20));
    var response =
        new LexiconBrowseController(Optional.of(repository)).browse(" ALIAS ", "frequency", 1, 20);
    assertEquals("42", response.items().getFirst().entryId());
    assertEquals("canonical", response.items().getFirst().lemma());
    assertEquals("eligible", response.items().getFirst().hintState());
    verify(repository).browse("alias", true, 1, 20);
  }

  @Test
  void rejectsUnboundedOrUnsupportedBrowseRequests() {
    var controller = new LexiconBrowseController(Optional.of(mock(LexiconReadRepository.class)));
    assertThrows(ResponseStatusException.class, () -> controller.browse("x", "random", 1, 20));
    assertThrows(ResponseStatusException.class, () -> controller.browse("x", "frequency", 1, 101));
    assertThrows(
        ResponseStatusException.class,
        () -> controller.browse("x".repeat(101), "frequency", 1, 20));
  }

  @Test
  void returnsServiceUnavailableWithoutPublishedRepository() {
    var controller = new LexiconBrowseController(Optional.empty());
    var error =
        assertThrows(
            ResponseStatusException.class, () -> controller.browse("", "frequency", 1, 20));
    assertEquals(HttpStatus.SERVICE_UNAVAILABLE, error.getStatusCode());
  }
}
