package io.lexiflow.lexicon.application.port;

/** 仅供基础设施工厂组合读写角色；业务调用者依赖所需的单一角色。 */
public interface LexiconRepository extends LexiconReadRepository, LexiconPublicationRepository {}
