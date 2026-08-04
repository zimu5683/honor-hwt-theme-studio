package io.github.zimu5683.hwttransfer

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

data class ReceiverUiState(
    val running: Boolean = false,
    val pairCode: String = "------",
    val codeExpiresAt: Long = 0L,
    val addresses: List<String> = emptyList(),
    val destination: String = "尚未授权 Honor/Themes",
    val lastTransfer: String = "暂无",
    val clients: List<PairedClient> = emptyList(),
    val error: String = "",
)

object ReceiverState {
    private val mutable = MutableStateFlow(ReceiverUiState())
    val state: StateFlow<ReceiverUiState> = mutable.asStateFlow()
    @Volatile var activityVisible: Boolean = false

    fun update(transform: (ReceiverUiState) -> ReceiverUiState) {
        mutable.update(transform)
    }
}
