pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        mavenCentral()
    }
}

rootProject.name = "lexiflow-backend"

includeBuild("gradle/build-logic")

mapOf(
    ":modules:foundation" to "modules/foundation",
    ":modules:identity" to "modules/identity",
    ":modules:lexicon" to "modules/lexicon",
    ":modules:vocabulary" to "modules/vocabulary",
    ":modules:content" to "modules/content",
    ":modules:learning" to "modules/learning",
    ":modules:semantic" to "modules/semantic",
    ":modules:enrichment" to "modules/enrichment",
    ":application:client-delivery" to "application/client-delivery",
    ":application:workflow" to "application/workflow",
    ":platform:persistence" to "platform/persistence",
    ":platform:cache" to "platform/cache",
    ":platform:security" to "platform/security",
    ":platform:observability" to "platform/observability",
    ":apps:api" to "apps/api",
    ":apps:worker" to "apps/worker",
    ":tests:architecture" to "tests/architecture",
    ":tests:quality-gates" to "tests/quality-gates",
).forEach { (path, directory) ->
    include(path)
    project(path).projectDir = file(directory)
}
