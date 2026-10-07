package io.lexiflow.buildlogic

import java.io.File

/** 执行阶段按本次构建 PATH 定位 Node，避免借用旧 Gradle daemon 的进程搜索路径。 */
internal fun nodeExecutable(searchPath: String): String =
    searchPath.split(File.pathSeparator).asSequence()
        .filter { it.isNotEmpty() }
        .map { File(it, "node") }
        .firstOrNull { it.isFile && it.canExecute() }
        ?.absolutePath
        ?: throw IllegalStateException("Node executable is required in the build PATH")
