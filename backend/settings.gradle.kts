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
    ":platform:adapters" to "platform/adapters",
    ":apps:api" to "apps/api",
    ":apps:worker" to "apps/worker",
    ":tests:architecture" to "tests/architecture",
    ":tests:quality-gates" to "tests/quality-gates",
    ":tests:integration" to "tests/integration",
).forEach { (path, directory) ->
    include(path)
    project(path).projectDir = file(directory)
}
