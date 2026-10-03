package io.lexiflow.buildlogic

object ReleaseVersion {
    @JvmStatic
    fun parse(raw: String): String {
        val value = when {
            raw.endsWith("\r\n") -> raw.dropLast(2)
            raw.endsWith("\n") -> raw.dropLast(1)
            else -> raw
        }
        require(Regex("(?:0|[1-9][0-9]{0,4})\\.(?:0|[1-9][0-9]{0,4})\\.(?:0|[1-9][0-9]{0,4})(?:-SNAPSHOT\\.g[a-f0-9]{7,64}(?:\\.dirty\\.[a-f0-9]{12,64})?)?").matches(value)) { "invalid software version" }
        val parts = value.substringBefore('-').split('.').map(String::toInt)
        require(parts.all { it <= 65535 } && parts.any { it != 0 }) { "invalid software version" }
        return value
    }
}
