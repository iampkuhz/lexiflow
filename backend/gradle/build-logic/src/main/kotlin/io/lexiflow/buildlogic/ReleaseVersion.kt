package io.lexiflow.buildlogic

import java.time.LocalDate
import java.time.format.DateTimeFormatter

object ReleaseVersion {
    /** 解析 ops/release/version.txt 的基础版本，格式为 major.minor.patch[-SNAPSHOT]。 */
    @JvmStatic
    fun parseBaseVersion(raw: String): String {
        val value = when {
            raw.endsWith("\r\n") -> raw.dropLast(2)
            raw.endsWith("\n") -> raw.dropLast(1)
            else -> raw
        }
        require(Regex("(?:0|[1-9][0-9]{0,4})\\.(?:0|[1-9][0-9]{0,4})\\.(?:0|[1-9][0-9]{0,4})(?:-SNAPSHOT)?").matches(value)) { "invalid software version" }
        val parts = value.substringBefore('-').split('.').map(String::toInt)
        require(parts.all { it <= 65535 } && parts.any { it != 0 }) { "invalid software version" }
        return value
    }

    /** 解析完整软件版本，包含源码提交日期、git hash 与 dirty 后缀。 */
    @JvmStatic
    fun parse(raw: String): String {
        val value = when {
            raw.endsWith("\r\n") -> raw.dropLast(2)
            raw.endsWith("\n") -> raw.dropLast(1)
            else -> raw
        }
        require(Regex("(?:0|[1-9][0-9]{0,4})\\.(?:0|[1-9][0-9]{0,4})\\.(?:0|[1-9][0-9]{0,4})(?:-SNAPSHOT\\.[1-9][0-9]{7}\\.g[a-f0-9]{7,64}(?:\\.dirty\\.[a-f0-9]{12,64})?)?").matches(value)) { "invalid software version" }
        if ("-SNAPSHOT." in value) {
            val date = value.substringAfter("-SNAPSHOT.").substringBefore('.')
            require(runCatching { LocalDate.parse(date, DateTimeFormatter.BASIC_ISO_DATE) }.isSuccess) { "invalid source date" }
        }
        val parts = value.substringBefore('-').split('.').map(String::toInt)
        require(parts.all { it <= 65535 } && parts.any { it != 0 }) { "invalid software version" }
        return value
    }
}
