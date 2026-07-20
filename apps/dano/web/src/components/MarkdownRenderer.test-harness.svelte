<script lang="ts">
  import MarkdownRenderer from "./MarkdownRenderer.svelte";

  let content = $state("| A | B |\n| :-");
  let streaming = $state(true);

  export function completeTable() {
    content = "| A | B |\n| :--- | ---: |\n| alpha | beta |";
    streaming = false;
  }
</script>

<MarkdownRenderer {content} {streaming} />
