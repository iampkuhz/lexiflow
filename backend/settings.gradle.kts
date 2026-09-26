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
    ":lexicon" to "product/lexicon",
    ":enrichment" to "product/enrichment",
    ":adapters" to "product/adapters",
    ":api" to "product/api",
    ":architecture-tests" to "verification/architecture",
    ":quality-gates" to "verification/quality-gates",
    ":integration-tests" to "verification/integration",
).forEach { (path, directory) ->
    include(path)
    project(path).projectDir = file(directory)
}
