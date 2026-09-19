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
    ":modules:lexicon" to "modules/lexicon",
    ":modules:enrichment" to "modules/enrichment",
    ":application:workflow" to "application/workflow",
    ":application:lexicon-application" to "application/lexicon",
    ":platform:adapters" to "platform/adapters",
    ":apps:api" to "apps/api",
    ":apps:worker" to "apps/worker",
    ":tests:architecture" to "tests/architecture",
    ":tests:quality-gates" to "tests/quality-gates",
).forEach { (path, directory) ->
    include(path)
    project(path).projectDir = file(directory)
}
